// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { RamsesV3PairedAdapter } from "./RamsesV3PairedAdapter.sol";
import { RamsesVaultAdapter } from "./RamsesVaultAdapter.sol";
import { RamsesOracleGuard } from "./RamsesOracleGuard.sol";
import { IRamsesPool } from "./interfaces/IRamsesV3.sol";

import { Initializable } from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import {
    AccessControlUpgradeable
} from "@openzeppelin/contracts-upgradeable/access/AccessControlUpgradeable.sol";
import {
    ReentrancyGuardUpgradeable
} from "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { IERC20Metadata } from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import { SafeERC20 } from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { SafeCast } from "@openzeppelin/contracts/utils/math/SafeCast.sol";

import { IStrategyLossReserve } from "baseline/src/interfaces/IStrategyLossReserve.sol";
import { RamsesConfigLib } from "./libraries/RamsesConfigLib.sol";
import { RamsesAccountingLib } from "./libraries/RamsesAccountingLib.sol";
import { RamsesRewardLib } from "./libraries/RamsesRewardLib.sol";
import { RamsesRangeLib } from "./libraries/RamsesRangeLib.sol";
import { RangePolicy, RangeState } from "./libraries/RangeTypes.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { RamsesSettlementLib } from "./libraries/RamsesSettlementLib.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";

/// @notice Separate Ramses deployment, porting the V3 accounting and guarded range policy.
/// No upgrade entry point. Initialization is restricted to the deploying account/factory.
contract RamsesBoostedVault is Initializable, AccessControlUpgradeable, ReentrancyGuardUpgradeable {
    using SafeERC20 for IERC20;
    using SafeCast for uint256;

    bytes32 public constant CONFIG_ROLE = keccak256("CONFIG_ROLE");
    bytes32 public constant KEEPER_ROLE = keccak256("KEEPER_ROLE");
    bytes32 public constant GUARDIAN_ROLE = keccak256("GUARDIAN_ROLE");
    uint256 internal constant BPS = 10_000;
    uint256 internal constant REMOVAL_OPERATIONAL_BUFFER_BPS = 100;

    RamsesOracleGuard public oracleGuard;
    IStrategyLossReserve public lossReserve;
    RamsesVaultAdapter public liquidityAdapter;

    mapping(bytes32 => PairConfig) private _pairConfig;
    mapping(bytes32 => PairLedger) private _ledger;
    mapping(address => uint256) public aggregateUsdgPrincipal;
    mapping(address => uint256) public aggregateUsdgDepositCap;

    mapping(bytes32 => uint16) public deployLossBps;
    /// @notice RAM and other non-native rewards: segregated custody, always zero NAV.
    mapping(bytes32 => mapping(address => uint256)) public externalRewards;

    mapping(bytes32 => RangePolicy) public rangePolicy;
    mapping(bytes32 => RangeState) public rangeState;

    error InvalidConfiguration();
    error UnknownPair();
    error UnsupportedToken();
    error UnauthorizedSide();
    error AllocationPaused();
    error SwapsPaused();
    error EmergencyMode();
    error InvalidDeadline();
    error CheckpointStale();
    error PairCapExceeded();
    error InsufficientPrincipal();
    error InsufficientLiquidity();
    error FeeOnTransferUnsupported();
    error GuardianCannotUnpause();
    error BalanceDeltaMismatch();
    error RangePolicyDisabled();

    event PairRegistered(
        bytes32 indexed pairId,
        address indexed stockToken,
        address indexed usdg,
        address stockAccount,
        address usdgAccount
    );
    event PairRiskUpdated(bytes32 indexed pairId, PairConfig config);
    event PairPauseUpdated(
        bytes32 indexed pairId, bool allocationPaused, bool swapsPaused, bool emergencyMode
    );
    event AggregateUsdgCapUpdated(address indexed usdg, uint256 previousCap, uint256 newCap);
    event Deposited(
        bytes32 indexed pairId, address indexed token, address indexed account, uint256 amount
    );
    event LiquidityRebalanced(
        bytes32 indexed pairId, uint256 stockUsed, uint256 usdgUsed, uint128 liquidityAdded
    );
    event FeesProcessed(
        bytes32 indexed pairId,
        uint256 stockFees,
        uint256 usdgFees,
        uint256 stockReserved,
        uint256 usdgReserved
    );
    event PairCheckpoint(
        bytes32 indexed pairId,
        uint256 stockAssets,
        uint256 usdgAssets,
        uint256 benchmarkUSDG,
        int256 pnlUSDG,
        uint256 stockAccountedAssets,
        uint256 usdgAccountedAssets
    );
    event Withdrawal(
        bytes32 indexed pairId,
        address indexed token,
        address indexed receiver,
        uint256 requested,
        uint256 returned,
        uint256 realizedLoss
    );
    event SettlementSwap(
        bytes32 indexed pairId, address indexed tokenIn, uint256 amountIn, uint256 amountOut
    );
    event RangePolicyUpdated(bytes32 indexed pairId, RangePolicy policy);
    event Recentered(
        bytes32 indexed pairId,
        int24 oldLower,
        int24 oldUpper,
        int24 newLower,
        int24 newUpper,
        uint128 liquidity,
        int24 centerTick
    );
    event EmergencyLiquidityDecreased(
        bytes32 indexed pairId, uint128 liquidity, uint256 stockReceived, uint256 usdgReceived
    );

    address private immutable _deployer = msg.sender;

    function initialize(
        address admin,
        address keeper,
        address guardian,
        RamsesOracleGuard oracleGuard_,
        IStrategyLossReserve lossReserve_,
        RamsesVaultAdapter liquidityAdapter_
    ) external initializer {
        if (msg.sender != _deployer) {
            revert InvalidConfiguration();
        }
        if (
            admin == address(0) || keeper == address(0) || guardian == address(0)
                || address(oracleGuard_) == address(0) || address(lossReserve_) == address(0)
                || address(liquidityAdapter_) == address(0)
        ) revert InvalidConfiguration();
        __AccessControl_init();
        __ReentrancyGuard_init();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(CONFIG_ROLE, admin);
        _grantRole(KEEPER_ROLE, keeper);
        _grantRole(GUARDIAN_ROLE, guardian);
        if (
            liquidityAdapter_.vault() != address(this)
                || address(liquidityAdapter_.oracle()) != address(oracleGuard_)
        ) revert InvalidConfiguration();
        oracleGuard = oracleGuard_;
        lossReserve = lossReserve_;
        liquidityAdapter = liquidityAdapter_;
    }

    function registerPair(
        bytes32 pairId,
        PairConfig calldata config,
        RamsesV3PairedAdapter.RegisterPairParams calldata adapterConfig
    ) external onlyRole(CONFIG_ROLE) nonReentrant {
        if (_pairConfig[pairId].exists) revert InvalidConfiguration();
        RamsesConfigLib.validatePairConfig(pairId, config);
        if (adapterConfig.stockToken != config.stockToken || adapterConfig.usdg != config.usdg) {
            revert InvalidConfiguration();
        }

        RamsesOracleGuard.FeedConfig memory feed = oracleGuard.feedConfig(pairId);
        if (
            feed.stockToken != config.stockToken || feed.usdg != config.usdg
                || address(feed.gauge) != address(adapterConfig.gauge)
                || adapterConfig.allocationDeviationBps < feed.maxPriceDeviationBps
        ) revert InvalidConfiguration();
        oracleGuard.pricesUSD18(pairId);
        oracleGuard.validatePoolPrice(pairId, adapterConfig.pool);
        // Round the half up: an odd bound would otherwise accept a tolerance one bp short of
        // the amount deviation it has to absorb.
        if (
            uint256(adapterConfig.removalToleranceBps)
                < (uint256(oracleGuard.maxRemovalDeviationBps(pairId)) + 1) / 2
                    + REMOVAL_OPERATIONAL_BUFFER_BPS
        ) revert InvalidConfiguration();
        liquidityAdapter.registerPair(pairId, adapterConfig);

        _pairConfig[pairId] = config;
        _pairConfig[pairId].exists = true;
        _ledger[pairId].lastCheckpoint = uint64(block.timestamp);

        emit PairRegistered(
            pairId, config.stockToken, config.usdg, config.stockAccount, config.usdgAccount
        );
    }

    event DeployLossBoundSet(bytes32 indexed pairId, uint16 bps);

    function setDeployLossBps(bytes32 pairId, uint16 bps) external onlyRole(CONFIG_ROLE) {
        _config(pairId);
        if (bps > 100) revert InvalidConfiguration();
        deployLossBps[pairId] = bps;
        emit DeployLossBoundSet(pairId, bps);
    }

    function setAggregateUsdgDepositCap(address usdg, uint256 newCap)
        external
        onlyRole(CONFIG_ROLE)
    {
        // A zero cap disables the optional aggregate circuit breaker. This is safe to
        // configure below current principal because it is not interpreted as a zero limit.
        if (usdg == address(0) || (newCap != 0 && newCap < aggregateUsdgPrincipal[usdg])) {
            revert InvalidConfiguration();
        }
        uint256 previous = aggregateUsdgDepositCap[usdg];
        aggregateUsdgDepositCap[usdg] = newCap;
        emit AggregateUsdgCapUpdated(usdg, previous, newCap);
    }

    function updatePairRisk(bytes32 pairId, PairConfig calldata config)
        external
        onlyRole(CONFIG_ROLE)
    {
        PairConfig storage current = _config(pairId);
        RamsesConfigLib.validatePairConfig(pairId, config);
        if (
            config.stockToken != current.stockToken || config.usdg != current.usdg
                || config.stockAccount != current.stockAccount
                || config.usdgAccount != current.usdgAccount
                || config.stockDecimals != current.stockDecimals
                || config.usdgDecimals != current.usdgDecimals
        ) revert InvalidConfiguration();
        _pairConfig[pairId] = config;
        _pairConfig[pairId].exists = true;
        emit PairRiskUpdated(pairId, config);
    }

    function setPairPause(
        bytes32 pairId,
        bool allocationPaused,
        bool swapsPaused,
        bool emergencyMode
    ) external {
        PairConfig storage config = _config(pairId);
        bool isAdmin = hasRole(CONFIG_ROLE, msg.sender);
        if (!isAdmin && !hasRole(GUARDIAN_ROLE, msg.sender)) {
            _checkRole(GUARDIAN_ROLE, msg.sender);
        }
        if (!isAdmin) {
            if (!allocationPaused || !swapsPaused || (config.emergencyMode && !emergencyMode)) {
                revert GuardianCannotUnpause();
            }
        }
        config.allocationPaused = allocationPaused;
        config.swapsPaused = swapsPaused;
        config.emergencyMode = emergencyMode;
        emit PairPauseUpdated(pairId, allocationPaused, swapsPaused, emergencyMode);
    }

    function pairConfig(bytes32 pairId) external view returns (PairConfig memory) {
        return _pairConfig[pairId];
    }

    function ledger(bytes32 pairId) external view returns (PairLedger memory) {
        return _ledger[pairId];
    }

    function accountedAssets(bytes32 pairId, address token) external view returns (uint256) {
        (, uint256 principal) = _sideAmounts(pairId, token);
        return principal;
    }

    /// @dev Shared token dispatch for the per-side views. Reverts on an unsupported token.
    function _sideAmounts(bytes32 pairId, address token)
        private
        view
        returns (uint256 idle, uint256 principal)
    {
        PairConfig storage config = _config(pairId);
        PairLedger storage pairLedger = _ledger[pairId];
        if (token == config.stockToken) return (pairLedger.stockIdle, pairLedger.stockPrincipal);
        if (token == config.usdg) return (pairLedger.usdgIdle, pairLedger.usdgPrincipal);
        revert UnsupportedToken();
    }

    function sideAccount(bytes32 pairId, address token) external view returns (address) {
        PairConfig storage config = _config(pairId);
        if (token == config.stockToken) return config.stockAccount;
        if (token == config.usdg) return config.usdgAccount;
        revert UnsupportedToken();
    }

    function liquidAssets(bytes32 pairId, address token) external view returns (uint256) {
        (uint256 idle,) = _sideAmounts(pairId, token);
        return idle;
    }

    /// @notice Assets for one side that `withdrawForSide` can return in this block.
    /// @dev Idle bypasses oracle settlement only when the position is closed and both
    ///      native claims are fully backed. Otherwise every principal reduction uses the guarded
    ///      settle path, so idle counts here only when that path would currently pass.
    ///      Integrating pTokens must use this, not `liquidAssets`, for cash and utilization
    ///      accounting; `liquidAssets` reports custody and overstates what is reachable.
    function withdrawableAssets(bytes32 pairId, address token) external view returns (uint256) {
        (uint256 idle, uint256 principal) = _sideAmounts(pairId, token);
        return RamsesAccountingLib.withdrawable(
            _config(pairId), _ledger[pairId], liquidityAdapter, oracleGuard, pairId, idle, principal
        );
    }

    function totalPairAssets(bytes32 pairId)
        public
        view
        returns (uint256 stockAssets, uint256 usdgAssets)
    {
        PairConfig storage config = _config(pairId);
        PairLedger storage pairLedger = _ledger[pairId];
        RamsesV3PairedAdapter.PositionState memory position = liquidityAdapter.positionState(pairId);
        stockAssets = pairLedger.stockIdle + position.stockAmount;
        usdgAssets = pairLedger.usdgIdle + position.usdgAmount;
        config;
    }

    /// @dev Pair assets with the position valued at `sqrtPriceX96` instead of the pool's live
    ///      price. Loss accounting uses the oracle-derived reference so that pushing the pool
    ///      cannot manufacture or mask a shortfall. `totalPairAssets` stays pool-priced and is
    ///      the market view.
    function _pairAssetsAt(bytes32 pairId, uint160 sqrtPriceX96)
        internal
        view
        returns (uint256 stockAssets, uint256 usdgAssets)
    {
        PairLedger storage pairLedger = _ledger[pairId];
        RamsesV3PairedAdapter.PositionState memory position =
            liquidityAdapter.positionStateAt(pairId, sqrtPriceX96);
        stockAssets = pairLedger.stockIdle + position.stockAmount;
        usdgAssets = pairLedger.usdgIdle + position.usdgAmount;
    }

    function depositForPair(bytes32 pairId, address token, uint256 amount)
        external
        nonReentrant
        returns (uint256 received)
    {
        PairConfig storage config = _config(pairId);
        if (config.allocationPaused) revert AllocationPaused();
        if (config.emergencyMode) revert EmergencyMode();
        if (amount == 0) return 0;

        PairLedger storage pairLedger = _ledger[pairId];
        if (token == config.stockToken) {
            if (msg.sender != config.stockAccount) revert UnauthorizedSide();
        } else if (token == config.usdg) {
            if (msg.sender != config.usdgAccount) revert UnauthorizedSide();
        } else {
            revert UnsupportedToken();
        }

        IERC20 asset = IERC20(token);
        uint256 senderBalanceBefore = asset.balanceOf(msg.sender);
        uint256 balanceBefore = asset.balanceOf(address(this));
        asset.safeTransferFrom(msg.sender, address(this), amount);
        uint256 senderBalanceAfter = asset.balanceOf(msg.sender);
        uint256 balanceAfter = asset.balanceOf(address(this));
        if (senderBalanceAfter > senderBalanceBefore || balanceAfter < balanceBefore) {
            revert FeeOnTransferUnsupported();
        }
        received = balanceAfter - balanceBefore;
        if (received != amount || senderBalanceBefore - senderBalanceAfter != amount) {
            revert FeeOnTransferUnsupported();
        }

        if (token == config.stockToken) {
            pairLedger.stockIdle += received;
            pairLedger.stockPrincipal += received;
        } else {
            pairLedger.usdgIdle += received;
            pairLedger.usdgPrincipal += received;
            aggregateUsdgPrincipal[config.usdg] += received;
            uint256 aggregateCap = aggregateUsdgDepositCap[config.usdg];
            if (aggregateCap != 0 && aggregateUsdgPrincipal[config.usdg] > aggregateCap) {
                revert PairCapExceeded();
            }
        }
        _enforcePairCap(pairId, config, pairLedger);
        emit Deposited(pairId, token, msg.sender, received);
    }

    function rebalance(bytes32 pairId, uint256 deadline)
        external
        onlyRole(KEEPER_ROLE)
        nonReentrant
    {
        PairConfig storage config = _config(pairId);
        if (config.allocationPaused) revert AllocationPaused();
        if (config.emergencyMode) revert EmergencyMode();
        _requireFresh(config, _ledger[pairId]);
        _checkDeadline(config, deadline);

        IRamsesPool key = liquidityAdapter.poolOf(pairId);
        (,, uint160 referenceSqrtPriceX96) = oracleGuard.validatePoolPrice(pairId, key);
        (uint256 stockPrice, uint256 usdgPrice) = oracleGuard.pricesUSD18(pairId);
        PairLedger storage pairLedger = _ledger[pairId];
        // v4 fee deltas are collected first so INCREASE_LIQUIDITY can safely use SETTLE_PAIR.
        _collectFees(pairId, config, pairLedger, deadline);
        if (RamsesAccountingLib.allocationCapExceeded(
                config,
                pairLedger,
                RamsesAccountingLib.Ctx(
                    pairId, liquidityAdapter, stockPrice, usdgPrice, referenceSqrtPriceX96
                )
            )) {
            config.allocationPaused = true;
            emit PairPauseUpdated(pairId, true, config.swapsPaused, config.emergencyMode);
            return;
        }
        RamsesRangeLib.Ctx memory c =
            _ctx(pairId, stockPrice, usdgPrice, referenceSqrtPriceX96, deadline);
        _deploy(pairId, config, pairLedger, c);
    }

    function _deploy(
        bytes32 pairId,
        PairConfig storage config,
        PairLedger storage pairLedger,
        RamsesRangeLib.Ctx memory c
    ) private {
        uint256 cap = type(uint256).max;
        uint16 lossBps = deployLossBps[pairId];
        if (_ranged(pairId)) {
            cap = RamsesRangeLib.remainingCapPerSide(config, rangePolicy[pairId], c);
        }
        (uint128 liquidityAdded, uint256 stockUsed, uint256 usdgUsed) =
            RamsesRangeLib.deploy(config, pairLedger, c, cap, lossBps, false);
        emit LiquidityRebalanced(pairId, stockUsed, usdgUsed, liquidityAdded);
        // Book what the (bounded) deployment cost at the oracle price now, so a supplier redeeming
        // from local market cash cannot exit at a rate that still includes it.
        _checkpointPair(pairId, config, pairLedger, c.deadline);
    }

    function _ctx(
        bytes32 pairId,
        uint256 stockPrice,
        uint256 usdgPrice,
        uint160 ref,
        uint256 deadline
    ) internal view returns (RamsesRangeLib.Ctx memory) {
        return RamsesRangeLib.Ctx({
            pairId: pairId,
            stockPrice: stockPrice,
            usdgPrice: usdgPrice,
            ref: ref,
            deadline: deadline,
            adapter: liquidityAdapter,
            guard: oracleGuard
        });
    }

    function _ranged(bytes32 pairId) internal view returns (bool ranged) {
        (,, ranged) = RamsesVaultAdapter(address(liquidityAdapter)).positionTicks(pairId);
    }

    // ---------------------------------------------------------------------------------
    // Concentrated range management
    // ---------------------------------------------------------------------------------

    function setRangePolicy(bytes32 pairId, RangePolicy calldata policy)
        external
        onlyRole(CONFIG_ROLE)
    {
        _config(pairId);
        RamsesRangeLib.validatePolicy(policy, liquidityAdapter.poolOf(pairId).tickSpacing());
        if (policy.enabled) _requireBandCoversGate(pairId);
        rangePolicy[pairId] = policy;
        emit RangePolicyUpdated(pairId, policy);
    }

    /// @dev A ranged position's removal floors are derived from the adapter's registered tolerance.
    /// They are only safe while that tolerance still covers the guard's CURRENT removal gate;
    /// otherwise a price inside the gate would make every exit revert on slippage.
    function _requireBandCoversGate(bytes32 pairId) internal view {
        uint256 tolerance = RamsesVaultAdapter(address(liquidityAdapter)).removalTolerance(pairId);
        if (
            tolerance <= REMOVAL_OPERATIONAL_BUFFER_BPS
                || 2 * (tolerance - REMOVAL_OPERATIONAL_BUFFER_BPS)
                    < oracleGuard.maxRemovalDeviationBps(pairId)
        ) revert InvalidConfiguration();
    }

    /// @notice Returns the pair to the legacy full range. Only while no position exists, so it
    /// is the last step of an exit (decrease, burn, clear), never a way to move live liquidity.
    function clearRange(bytes32 pairId) external onlyRole(KEEPER_ROLE) nonReentrant {
        _config(pairId);
        RamsesVaultAdapter(address(liquidityAdapter)).clearRange(pairId);
        // Keep the recenter history: clearing must not reset the cooldown or the rolling budget.
        rangeState[pairId].initialized = false;
    }

    /// @notice Re-centres the ranged position on the oracle price. The keeper only chooses
    /// WHEN; the range, the amounts and every limit are derived or checked on chain.
    function recenter(bytes32 pairId, uint256 deadline)
        external
        onlyRole(KEEPER_ROLE)
        nonReentrant
    {
        PairConfig storage config = _config(pairId);
        if (!rangePolicy[pairId].enabled) revert RangePolicyDisabled();
        _requireBandCoversGate(pairId);
        if (config.allocationPaused) revert AllocationPaused();
        if (config.emergencyMode) revert EmergencyMode();
        _checkDeadline(config, deadline);
        PairLedger storage pairLedger = _ledger[pairId];

        _recenter(pairId, config, pairLedger, deadline);
    }

    function _recenter(
        bytes32 pairId,
        PairConfig storage config,
        PairLedger storage pairLedger,
        uint256 deadline
    ) private {
        // Settle everything that already happened at the oracle reference price first.
        (, uint256 stockPrice, uint256 usdgPrice, uint160 ref) =
            _checkpointPair(pairId, config, pairLedger, deadline);
        _receiveRewards(pairId, config, pairLedger, deadline, true);
        if (RamsesAccountingLib.allocationCapExceeded(
                config,
                pairLedger,
                RamsesAccountingLib.Ctx(pairId, liquidityAdapter, stockPrice, usdgPrice, ref)
            )) {
            // Same circuit breaker as rebalance: never put more capital to work above the cap.
            config.allocationPaused = true;
            emit PairPauseUpdated(pairId, true, config.swapsPaused, config.emergencyMode);
            return;
        }
        RamsesRangeLib.recenter(
            config,
            pairLedger,
            rangePolicy[pairId],
            rangeState[pairId],
            _ctx(pairId, stockPrice, usdgPrice, ref, deadline),
            deployLossBps[pairId]
        );
        // Book the recenter's own bounded execution loss at the same oracle reference.
        _checkpointPair(pairId, config, pairLedger, deadline);
        // `Recentered` is emitted by RamsesRangeLib (same topic, from the vault address) to save bytecode.
    }

    function collectFees(bytes32 pairId, uint256 deadline)
        external
        onlyRole(KEEPER_ROLE)
        nonReentrant
        returns (uint256 stockFees, uint256 usdgFees)
    {
        PairConfig storage config = _config(pairId);
        _checkDeadline(config, deadline);
        return _collectFees(pairId, config, _ledger[pairId], deadline);
    }

    function checkpoint(bytes32 pairId, uint256 deadline)
        external
        onlyRole(KEEPER_ROLE)
        nonReentrant
        returns (int256 pnlUSDG)
    {
        PairConfig storage config = _config(pairId);
        if (config.emergencyMode) revert EmergencyMode();
        _checkDeadline(config, deadline);
        PairLedger storage pairLedger = _ledger[pairId];
        (pnlUSDG,,,) = _checkpointPair(pairId, config, pairLedger, deadline);
    }

    function withdrawForSide(
        bytes32 pairId,
        address token,
        uint256 requested,
        address receiver,
        uint256 deadline
    ) external nonReentrant returns (uint256 returned, uint256 realizedLoss) {
        PairConfig storage config = _config(pairId);
        if (receiver == address(0) || receiver == address(this)) revert InvalidConfiguration();
        bool stockSide = _authorizeSide(config, token);
        PairLedger storage pairLedger = _ledger[pairId];
        uint256 principalBefore = stockSide ? pairLedger.stockPrincipal : pairLedger.usdgPrincipal;
        if (requested > principalBefore) revert InsufficientPrincipal();
        if (requested == 0) return (0, 0);

        uint256 idle = stockSide ? pairLedger.stockIdle : pairLedger.usdgIdle;
        bool hasOpenLiquidity = liquidityAdapter.positionState(pairId).liquidity != 0;
        // Idle remains part of the shared benchmark while LP liquidity or a native deficit remains.
        // Settle current strategy losses before either side can reduce its claim, even
        // when that side's requested token is already available in the vault.
        if (idle < requested || hasOpenLiquidity || !_nativeClaimsCovered(pairLedger)) {
            if (config.emergencyMode) revert EmergencyMode();
            _checkDeadline(config, deadline);
            uint256 stockPrice;
            uint256 usdgPrice;
            // Derived from oracle prices alone, so it is constant for the whole call and can
            // be reused after the unwind and settlement without revalidating.
            uint160 referenceSqrtPriceX96;
            if (_checkpointIsStale(config, pairLedger)) {
                (, stockPrice, usdgPrice, referenceSqrtPriceX96) =
                    _checkpointPair(pairId, config, pairLedger, deadline);
            } else {
                IRamsesPool key = liquidityAdapter.poolOf(pairId);
                (,, referenceSqrtPriceX96) = oracleGuard.validateRemovalPrice(pairId, key);
                (stockPrice, usdgPrice) = oracleGuard.pricesUSD18(pairId);
                _collectFees(pairId, config, pairLedger, deadline);
            }

            _unwindForWithdrawal(pairId, config, pairLedger, stockSide, requested, deadline);
            _settleShortfall(
                pairId, config, pairLedger, stockSide, requested, stockPrice, usdgPrice, deadline
            );
            _recognizeLoss(pairId, config, pairLedger, stockPrice, usdgPrice, referenceSqrtPriceX96);
        }

        uint256 finalIdle = stockSide ? pairLedger.stockIdle : pairLedger.usdgIdle;
        uint256 finalPrincipal = stockSide ? pairLedger.stockPrincipal : pairLedger.usdgPrincipal;
        returned = Math.min(requested, Math.min(finalIdle, finalPrincipal));
        if (stockSide) {
            pairLedger.stockIdle -= returned;
            pairLedger.stockPrincipal -= returned;
        } else {
            pairLedger.usdgIdle -= returned;
            pairLedger.usdgPrincipal -= returned;
            aggregateUsdgPrincipal[config.usdg] -= returned;
        }
        uint256 principalAfter = stockSide ? pairLedger.stockPrincipal : pairLedger.usdgPrincipal;
        // Report every claim reduction realized inside this withdrawal, including a
        // checkpoint loss that also reduces the caller's remaining strategy claim.
        // This lets a future pToken apply returned assets and loss atomically.
        if (principalBefore > principalAfter) {
            uint256 totalClaimReduction = principalBefore - principalAfter;
            if (totalClaimReduction > returned) realizedLoss = totalClaimReduction - returned;
        }
        _pushExact(token, receiver, returned);
        emit Withdrawal(pairId, token, receiver, requested, returned, realizedLoss);
    }

    function emergencyDecrease(bytes32 pairId, uint128 liquidity, uint256 deadline)
        external
        onlyRole(GUARDIAN_ROLE)
        nonReentrant
    {
        PairConfig storage config = _config(pairId);
        _checkDeadline(config, deadline);
        config.allocationPaused = true;
        config.swapsPaused = true;
        config.emergencyMode = true;
        IRamsesPool key = liquidityAdapter.poolOf(pairId);
        (,, uint160 referenceSqrtPriceX96) = oracleGuard.validateRemovalPrice(pairId, key);
        (uint256 stockPrice, uint256 usdgPrice) = oracleGuard.pricesUSD18(pairId);
        _collectFees(pairId, config, _ledger[pairId], deadline);
        uint256 stockBalanceBefore = IERC20(config.stockToken).balanceOf(address(this));
        uint256 usdgBalanceBefore = IERC20(config.usdg).balanceOf(address(this));
        (uint256 stockReceived, uint256 usdgReceived, uint128 liquidityRemoved) =
            liquidityAdapter.decreaseLiquidity(pairId, liquidity, referenceSqrtPriceX96, deadline);
        _requireBalanceIncrease(config.stockToken, stockBalanceBefore, stockReceived);
        _requireBalanceIncrease(config.usdg, usdgBalanceBefore, usdgReceived);
        PairLedger storage pairLedger = _ledger[pairId];
        pairLedger.stockIdle += stockReceived;
        pairLedger.usdgIdle += usdgReceived;
        // Allocate any shared LP loss atomically with the guardian exit. If this was
        // only a partial exit, withdrawForSide remains closed while liquidity remains.
        _recognizeLoss(pairId, config, pairLedger, stockPrice, usdgPrice, referenceSqrtPriceX96);
        emit EmergencyLiquidityDecreased(pairId, liquidityRemoved, stockReceived, usdgReceived);
        emit PairPauseUpdated(pairId, true, true, true);
    }

    function burnEmptyPosition(bytes32 pairId, uint256 deadline)
        external
        onlyRole(KEEPER_ROLE)
        nonReentrant
    {
        PairConfig storage config = _config(pairId);
        _checkDeadline(config, deadline);
        uint256 stockBalanceBefore = IERC20(config.stockToken).balanceOf(address(this));
        uint256 usdgBalanceBefore = IERC20(config.usdg).balanceOf(address(this));
        (uint256 stockReceived, uint256 usdgReceived) =
            liquidityAdapter.burnEmptyPosition(pairId, deadline);
        _requireBalanceIncrease(config.stockToken, stockBalanceBefore, stockReceived);
        _requireBalanceIncrease(config.usdg, usdgBalanceBefore, usdgReceived);
        _ledger[pairId].stockIdle += stockReceived;
        _ledger[pairId].usdgIdle += usdgReceived;
    }

    function _collectFees(
        bytes32 pairId,
        PairConfig storage config,
        PairLedger storage pairLedger,
        uint256 deadline
    ) internal returns (uint256 stockFees, uint256 usdgFees) {
        uint256 stockBalanceBefore = IERC20(config.stockToken).balanceOf(address(this));
        uint256 usdgBalanceBefore = IERC20(config.usdg).balanceOf(address(this));
        (stockFees, usdgFees) = liquidityAdapter.collectFees(pairId, deadline);
        _requireBalanceIncrease(config.stockToken, stockBalanceBefore, stockFees);
        _requireBalanceIncrease(config.usdg, usdgBalanceBefore, usdgFees);
        RamsesRewardLib.credit(
            config, pairLedger, aggregateUsdgPrincipal, pairId, lossReserve, stockFees, usdgFees
        );
    }

    /// @notice Keepers cannot opt into reward destruction, including during emergencies.
    function setAllowRewardForfeiture(bytes32 pairId, bool allowed)
        external
        onlyRole(CONFIG_ROLE)
        nonReentrant
    {
        _config(pairId);
        liquidityAdapter.setAllowRewardForfeiture(pairId, allowed);
    }

    event ExternalRewardForwarded(
        bytes32 indexed pairId, address indexed token, address indexed receiver, uint256 amount
    );

    function forwardExternalReward(bytes32 pairId, address token, address receiver, uint256 amount)
        external
        onlyRole(CONFIG_ROLE)
        nonReentrant
    {
        PairConfig storage c = _config(pairId);
        if (
            token == c.stockToken || token == c.usdg || receiver == address(0)
                || receiver == address(this)
        ) revert InvalidConfiguration();
        externalRewards[pairId][token] -= amount;
        _pushExact(token, receiver, amount);
        emit ExternalRewardForwarded(pairId, token, receiver, amount);
    }

    function harvest(bytes32 pairId, uint256 deadline) external onlyRole(KEEPER_ROLE) nonReentrant {
        PairConfig storage c = _config(pairId);
        _checkDeadline(c, deadline);
        _checkpointPair(pairId, c, _ledger[pairId], deadline);
        _receiveRewards(pairId, c, _ledger[pairId], deadline, true);
    }

    /// @notice Previously claimed rewards stay recoverable independently of gauge/feed health.
    function claimPendingRewards(bytes32 pairId) external onlyRole(KEEPER_ROLE) nonReentrant {
        _receiveRewards(pairId, _config(pairId), _ledger[pairId], block.timestamp, false);
    }

    function _receiveRewards(
        bytes32 id,
        PairConfig storage c,
        PairLedger storage l,
        uint256 deadline,
        bool fresh
    ) private {
        RamsesRewardLib.receiveRewards(
            c,
            l,
            aggregateUsdgPrincipal,
            externalRewards[id],
            RamsesRewardLib.Ctx(id, liquidityAdapter, oracleGuard, lossReserve, deadline, fresh)
        );
    }

    function _checkpointPair(
        bytes32 pairId,
        PairConfig storage config,
        PairLedger storage pairLedger,
        uint256 deadline
    )
        internal
        returns (
            int256 pnlUSDG,
            uint256 stockPrice,
            uint256 usdgPrice,
            uint160 referenceSqrtPriceX96
        )
    {
        IRamsesPool key = liquidityAdapter.poolOf(pairId);
        (,, referenceSqrtPriceX96) = oracleGuard.validateRemovalPrice(pairId, key);
        (stockPrice, usdgPrice) = oracleGuard.pricesUSD18(pairId);

        _collectFees(pairId, config, pairLedger, deadline);
        pnlUSDG = RamsesAccountingLib.checkpoint(
            config,
            pairLedger,
            aggregateUsdgPrincipal,
            RamsesAccountingLib.Ctx(
                pairId, liquidityAdapter, stockPrice, usdgPrice, referenceSqrtPriceX96
            ),
            true
        );
    }

    function _unwindForWithdrawal(
        bytes32 pairId,
        PairConfig storage config,
        PairLedger storage pairLedger,
        bool stockSide,
        uint256 requested,
        uint256 deadline
    ) internal {
        uint256 idle = stockSide ? pairLedger.stockIdle : pairLedger.usdgIdle;
        if (idle >= requested) return;
        RamsesV3PairedAdapter.PositionState memory position = liquidityAdapter.positionState(pairId);
        if (position.liquidity == 0) return;

        uint256 shortfall = requested - idle;
        uint256 targetInPosition = stockSide ? position.stockAmount : position.usdgAmount;
        if (targetInPosition == 0) {
            // None of the requested token is in the position (a one-sided range, or a position
            // that migrated into the other token). With settlement swaps paused nothing can turn
            // the counter token into it, so removing liquidity would only churn the position.
            if (config.swapsPaused) return;
            // With swaps enabled the counter token is exactly what the bounded swap converts:
            // free the whole position (V2 behaviour) so the settlement has inventory.
            targetInPosition = 1;
            shortfall = 1;
        }
        uint256 targetAmount =
            Math.mulDiv(shortfall, BPS + config.withdrawOverUnwindBps, BPS, Math.Rounding.Ceil);
        uint256 liquidityRaw = Math.mulDiv(
            position.liquidity,
            Math.min(targetAmount, targetInPosition),
            targetInPosition,
            Math.Rounding.Ceil
        );
        uint128 liquidityToRemove = uint128(Math.min(liquidityRaw, position.liquidity));
        IRamsesPool key = liquidityAdapter.poolOf(pairId);
        (,, uint160 referenceSqrtPriceX96) = oracleGuard.validateRemovalPrice(pairId, key);
        uint256 stockBalanceBefore = IERC20(config.stockToken).balanceOf(address(this));
        uint256 usdgBalanceBefore = IERC20(config.usdg).balanceOf(address(this));
        (uint256 stockReceived, uint256 usdgReceived,) = liquidityAdapter.decreaseLiquidity(
            pairId, liquidityToRemove, referenceSqrtPriceX96, deadline
        );
        _requireBalanceIncrease(config.stockToken, stockBalanceBefore, stockReceived);
        _requireBalanceIncrease(config.usdg, usdgBalanceBefore, usdgReceived);
        pairLedger.stockIdle += stockReceived;
        pairLedger.usdgIdle += usdgReceived;
    }

    function _settleShortfall(
        bytes32 pairId,
        PairConfig storage config,
        PairLedger storage pairLedger,
        bool stockSide,
        uint256 requested,
        uint256 stockPrice,
        uint256 usdgPrice,
        uint256 deadline
    ) internal {
        RamsesSettlementLib.settleShortfall(
            config,
            pairLedger,
            RamsesSettlementLib.SettleParams({
                pairId: pairId,
                stockSide: stockSide,
                requested: requested,
                stockPrice: stockPrice,
                usdgPrice: usdgPrice,
                deadline: deadline,
                lossReserve: lossReserve,
                oracleGuard: oracleGuard,
                liquidityAdapter: liquidityAdapter
            })
        );
    }

    /// @dev Zero LP liquidity does not imply zero shared exposure: a native-token
    ///      deficit may still be backed by the other side's surplus. Only fully
    ///      in-kind-backed claims may bypass price/loss settlement.
    function _nativeClaimsCovered(PairLedger storage pairLedger) internal view returns (bool) {
        return pairLedger.stockIdle >= pairLedger.stockPrincipal
            && pairLedger.usdgIdle >= pairLedger.usdgPrincipal;
    }

    function _recognizeLoss(
        bytes32 pairId,
        PairConfig storage config,
        PairLedger storage pairLedger,
        uint256 stockPrice,
        uint256 usdgPrice,
        uint160 referenceSqrtPriceX96
    ) internal {
        RamsesAccountingLib.checkpoint(
            config,
            pairLedger,
            aggregateUsdgPrincipal,
            RamsesAccountingLib.Ctx(
                pairId, liquidityAdapter, stockPrice, usdgPrice, referenceSqrtPriceX96
            ),
            false
        );
    }

    function _enforcePairCap(
        bytes32 pairId,
        PairConfig storage config,
        PairLedger storage pairLedger
    ) internal view {
        (uint256 stockPrice, uint256 usdgPrice) = oracleGuard.pricesUSD18(pairId);
        if (_pairCapExceeded(config, pairLedger, stockPrice, usdgPrice)) revert PairCapExceeded();
    }

    function _pairCapExceeded(
        PairConfig storage config,
        PairLedger storage pairLedger,
        uint256 stockPrice,
        uint256 usdgPrice
    ) internal view returns (bool) {
        return config.maxPairValueUSDG != 0
            && _benchmarkUSDG(config, pairLedger, stockPrice, usdgPrice) > config.maxPairValueUSDG;
    }

    function _benchmarkUSDG(
        PairConfig storage config,
        PairLedger storage pairLedger,
        uint256 stockPrice,
        uint256 usdgPrice
    ) internal view returns (uint256) {
        return _grossUSDG(
            config, pairLedger.stockPrincipal, pairLedger.usdgPrincipal, stockPrice, usdgPrice
        );
    }

    function _grossUSDG(
        PairConfig storage config,
        uint256 stockAmount,
        uint256 usdgAmount,
        uint256 stockPrice,
        uint256 usdgPrice
    ) internal view returns (uint256) {
        return VaultMath.valueUSD18(
            stockAmount, config.stockDecimals, stockPrice, Math.Rounding.Floor
        ) + VaultMath.valueUSD18(usdgAmount, config.usdgDecimals, usdgPrice, Math.Rounding.Floor);
    }

    function _authorizeSide(PairConfig storage config, address token)
        internal
        view
        returns (bool stockSide)
    {
        if (token == config.stockToken) {
            if (msg.sender != config.stockAccount) revert UnauthorizedSide();
            return true;
        }
        if (token == config.usdg) {
            if (msg.sender != config.usdgAccount) revert UnauthorizedSide();
            return false;
        }
        revert UnsupportedToken();
    }

    function _checkpointIsStale(PairConfig storage config, PairLedger storage pairLedger)
        internal
        view
        returns (bool)
    {
        return pairLedger.lastCheckpoint == 0
            || block.timestamp - pairLedger.lastCheckpoint > config.maxCheckpointAge;
    }

    function _requireFresh(PairConfig storage config, PairLedger storage pairLedger) internal view {
        if (_checkpointIsStale(config, pairLedger)) revert CheckpointStale();
    }

    function _observedBalanceIncrease(address token, uint256 balanceBefore)
        internal
        view
        returns (uint256 observed)
    {
        uint256 balanceAfter = IERC20(token).balanceOf(address(this));
        if (balanceAfter < balanceBefore) revert BalanceDeltaMismatch();
        observed = balanceAfter - balanceBefore;
    }

    function _requireBalanceIncrease(address token, uint256 balanceBefore, uint256 expected)
        internal
        view
    {
        if (_observedBalanceIncrease(token, balanceBefore) != expected) {
            revert BalanceDeltaMismatch();
        }
    }

    function _requireBalanceDecrease(address token, uint256 balanceBefore, uint256 expected)
        internal
        view
    {
        uint256 balanceAfter = IERC20(token).balanceOf(address(this));
        if (balanceAfter > balanceBefore || balanceBefore - balanceAfter != expected) {
            revert BalanceDeltaMismatch();
        }
    }

    function _pushExact(address token, address receiver, uint256 amount) internal {
        IERC20 asset = IERC20(token);
        uint256 senderBefore = asset.balanceOf(address(this));
        uint256 receiverBefore = asset.balanceOf(receiver);
        asset.safeTransfer(receiver, amount);
        uint256 senderAfter = asset.balanceOf(address(this));
        uint256 receiverAfter = asset.balanceOf(receiver);
        if (
            senderAfter > senderBefore || receiverAfter < receiverBefore
                || senderBefore - senderAfter != amount || receiverAfter - receiverBefore != amount
        ) revert BalanceDeltaMismatch();
    }

    function _checkDeadline(PairConfig storage config, uint256 deadline) internal view {
        if (deadline < block.timestamp) revert InvalidDeadline();
        uint256 delay = deadline - block.timestamp;
        if (delay > config.maxDeadlineDelay) revert InvalidDeadline();
    }

    function _config(bytes32 pairId) internal view returns (PairConfig storage config) {
        config = _pairConfig[pairId];
        if (!config.exists) revert UnknownPair();
    }
}
