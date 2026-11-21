// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

// Import order matters: PErc20 first, or solc reports a base-before-derived error in PToken.sol.
import "peridot/PErc20.sol";
import { PeridottrollerInterface } from "peridot/PeridottrollerInterface.sol";
import { InterestRateModel } from "peridot/InterestRateModel.sol";
import { PErc20Delegator } from "peridot/PErc20Delegator.sol";
import { Test, Vm } from "forge-std/Test.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";
import { PoolKey } from "@uniswap/v4-core/src/types/PoolKey.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { IUniswapV4PairedAdapter } from "baseline/src/interfaces/IUniswapV4PairedAdapter.sol";
import { StockOracleGuard } from "baseline/src/StockOracleGuard.sol";
import { StrategyLossReserve } from "baseline/src/StrategyLossReserve.sol";
import { RangePolicy } from "../../src/libraries/RangeTypes.sol";
import { RobinhoodLendingPriceAdapterN } from "../../src/RobinhoodLendingPriceAdapterN.sol";

interface IOptVault {
    function pairConfig(bytes32) external view returns (PairConfig memory);
    function ledger(bytes32) external view returns (PairLedger memory);
    function registerPair(
        bytes32,
        PairConfig calldata,
        IUniswapV4PairedAdapter.RegisterPairParams calldata
    ) external;
    function setRangePolicy(bytes32, RangePolicy calldata) external;
    function rangePolicy(bytes32)
        external
        view
        returns (bool, uint16, uint16, uint32, uint8, uint16, uint128);
    function checkpoint(bytes32, uint256) external;
    function rebalance(bytes32, uint256) external;
    function recenter(bytes32, uint256) external;
    function withdrawableAssets(bytes32, address) external view returns (uint256);
    function setPairPause(bytes32, bool, bool, bool) external;
}

interface IOptAdapter {
    function poolKey(bytes32) external view returns (PoolKey memory);
    function positionState(bytes32) external view returns (IUniswapV4PairedAdapter.PositionState memory);
    function removalTolerance(bytes32) external view returns (uint16);
}

interface IOptGuard {
    function feedConfig(bytes32) external view returns (StockOracleGuard.FeedConfig memory);
    function configurePair(bytes32, StockOracleGuard.FeedConfig calldata) external;
}

interface IOptReserve {
    function configurePair(bytes32, StrategyLossReserve.ReserveConfig calldata) external;
    function deposit(bytes32, address, uint256) external returns (uint256);
    function available(bytes32, address) external view returns (uint256);
}

interface IOptController {
    function oracle() external view returns (address);
    function _setPriceOracle(address) external returns (uint256);
    function _supportMarket(address) external returns (uint256);
    function _setBorrowPaused(address, bool) external returns (bool);
    function _setMintPaused(address, bool) external returns (bool);
    function markets(address) external view returns (bool, uint256, bool);
    function enterMarkets(address[] calldata) external returns (uint256[] memory);
    function getAccountLiquidity(address) external view returns (uint256, uint256, uint256);
    function borrowGuardianPaused(address) external view returns (bool);
    function mintGuardianPaused(address) external view returns (bool);
    function getAllMarkets() external view returns (address[] memory);
}

interface IOptMarket {
    function mint(uint256) external returns (uint256);
    function redeem(uint256) external returns (uint256);
    function borrow(uint256) external returns (uint256);
    function balanceOf(address) external view returns (uint256);
    function transfer(address, uint256) external returns (bool);
    function totalSupply() external view returns (uint256);
    function totalBorrows() external view returns (uint256);
    function exchangeRateCurrent() external returns (uint256);
    function exchangeRateStored() external view returns (uint256);
    function getCash() external view returns (uint256);
    function accrueInterest() external returns (uint256);
    function queueSetVaultConfig(address, bytes32, uint256, address) external returns (bytes32);
    function _setVaultConfig(address, bytes32, uint256, address) external;
    function queueSetVaultPaused(bool) external returns (bytes32);
    function _setVaultPaused(bool) external;
    function vaultPaused() external view returns (bool);
    function vaultAccountedAssets() external view returns (uint256);
    function vaultWithdrawableAssets() external view returns (uint256);
    function robinhoodPairId() external view returns (bytes32);
    function borrowAccountingEnabled() external view returns (bool);
}

interface IOptERC20 {
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
    function transfer(address, uint256) external returns (bool);
}

interface IOptPriceOracle {
    function getUnderlyingPrice(address) external view returns (uint256);
}

/// Fixed-answer, always-fresh Chainlink stand-in, etched over the NVDA 24/7 feed for the price-shock tests only.
contract FreshFeedMock {
    int256 public answer; // slot 0

    function decimals() external pure returns (uint8) {
        return 8;
    }

    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80) {
        return (1, answer, block.timestamp, block.timestamp, 1);
    }
}

/// @notice NVDA opt-in vault (pair NVDA/USDG/OPTIN on the live Uniswap vault 0x2808, markets upNVDA / upUSDG on
/// controller 0x6148). The ceremony is replayed here exactly as tools/nvda-optin/ceremony.sh sends it (Ledger
/// impersonated), on top of a fork that ALREADY carries the SPY-margin and pSPY-collateral ceremonies (the anvil
/// rehearsal applies them with their own scripts first). Production pair, plain markets and margin are checked to be
/// untouched; the opt-in lifecycle is exercised with real pool, vault, adapter, guard and reserve code.
///
/// Env: NVDA_OPTIN_FORK_RPC (anvil after the SPY ceremonies; default ROBINHOOD_RPC_URL). Set NVDA_OPTIN_ALLOW_PRE_SPY=true
/// to run against a chain where the SPY-margin ceremony has not run (the pSPY price parity check is then skipped).
contract NvdaOptInForkTest is Test {
    address constant LEDGER = 0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD;
    address constant CONTROLLER = 0x6148183676E304dbe63a85C350c208DA3cEAc39C;
    address constant ADAPTER3 = 0x74f1ea31ED6615833F096D1Ac695036538969A59;
    address constant LENDING_SOURCE = 0x266F014d1325774F1190f963Df4369E07dDA1d33;
    address constant DELEGATE = 0x0C6F6962d80390F6104f80811a150e8233bd8FF1;
    address constant IRM = 0x0987154fB5676a8Ea545AAf41F8ef2492F785d22;
    address constant PNVDA = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    address constant PUSDG = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    address constant PSPY = 0xa6660499a9cB9B6691247fE1FAbc248f502E5fB3;
    address constant NVDA = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address constant USDG = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    address constant ADAPTER = 0xadA73211711e4790bc83B5d6B39f47fE04D276f3;
    address constant GUARD = 0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741;
    address constant RESERVE = 0x806b182B050f7EcF908758dD6bBF91DB8B2212aF;
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    address constant RANGE_KEEPER = 0x82ABD8eE038c10F5bE00E5200C94265400999f7e;
    address constant NVDA_FEED = 0x321A36e441DfBadA01Aa6f9884F14a344E133D1F;
    address constant EXECUTOR = 0x6A45Ae86bD992d250580d08D340A06A04D478977;
    address constant RISK = 0xC8b178C3c74570472FF1eeE0DD559e61AF9f9678;
    address constant NVDA_LENDER = 0x12c1e2C33F63F897E0e4ac1969C29493136f2481;
    address constant WHALE = 0x8366a39CC670B4001A1121B8F6A443A643e40951;
    address constant DEAD = 0x000000000000000000000000000000000000dEaD;
    bytes32 constant PROD = keccak256("NVDA/USDG");
    bytes32 constant OPTIN = keccak256("NVDA/USDG/OPTIN");
    bytes32 constant SALT = keccak256("peridot-nvda-optin-v1");
    bytes32 constant DEPOSIT_FAILED = keccak256("VaultDepositFailed(uint256)");

    uint256 constant SEED_NVDA = 0.02e18;
    uint256 constant SEED_USDG = 4e6;
    uint256 constant RESERVE_NVDA = 0.03e18;
    uint256 constant RESERVE_USDG = 5e6;

    IOptVault internal vault = IOptVault(VAULT);
    IOptController internal controller = IOptController(CONTROLLER);
    IOptMarket internal upNvda;
    IOptMarket internal upUsdg;
    RobinhoodLendingPriceAdapterN internal adapterN;
    bool internal postSpy;

    PairLedger internal prodLedgerBefore;
    uint256 internal prodReserveNvdaBefore;
    bytes internal untouchedBefore;
    bytes internal untouchedAfterT0;

    function setUp() public {
        string memory rpc = vm.envOr("NVDA_OPTIN_FORK_RPC", vm.envOr("ROBINHOOD_RPC_URL", string("")));
        if (bytes(rpc).length == 0) {
            vm.skip(true);
            return;
        }
        vm.createSelectFork(rpc);
        assertEq(block.chainid, 4663);
        postSpy = controller.oracle() == ADAPTER3;
        if (!postSpy) require(vm.envOr("NVDA_OPTIN_ALLOW_PRE_SPY", false), "RUN_AFTER_SPY_CEREMONIES");
        // Fork-only: accrue the live markets on this node's block number (the chain accrues on the native L1
        // number; anvil and forge report the L2 height). Same adjustment as the SPY rehearsals (slot 9).
        vm.store(PUSDG, bytes32(uint256(9)), bytes32(block.number));
        vm.store(PNVDA, bytes32(uint256(9)), bytes32(block.number));

        prodLedgerBefore = vault.ledger(PROD);
        prodReserveNvdaBefore = IOptReserve(RESERVE).available(PROD, NVDA);
        _deploy();
        untouchedBefore = _untouched();
        _t0();
        untouchedAfterT0 = _untouched();
        vm.warp(block.timestamp + 3601);
        _t1();
    }

    // ---------------------------------------------------------------- ceremony replay (mirrors ceremony.sh)

    function _deploy() internal {
        address deployer = makeAddr("throwaway-deployer");
        vm.startPrank(deployer);
        upNvda = IOptMarket(
            address(
                new PErc20Delegator(
                    NVDA,
                    PeridottrollerInterface(CONTROLLER),
                    InterestRateModel(IRM),
                    2e26,
                    "Peridot NVDA Uniswap LP",
                    "upNVDA",
                    8,
                    payable(LEDGER),
                    DELEGATE,
                    ""
                )
            )
        );
        upUsdg = IOptMarket(
            address(
                new PErc20Delegator(
                    USDG,
                    PeridottrollerInterface(CONTROLLER),
                    InterestRateModel(IRM),
                    2e14,
                    "Peridot USDG Uniswap LP",
                    "upUSDG",
                    8,
                    payable(LEDGER),
                    DELEGATE,
                    ""
                )
            )
        );
        address[] memory ms = new address[](5);
        ms[0] = PNVDA;
        ms[1] = PUSDG;
        ms[2] = PSPY;
        ms[3] = address(upNvda);
        ms[4] = address(upUsdg);
        adapterN = new RobinhoodLendingPriceAdapterN(LENDING_SOURCE, ms);
        vm.stopPrank();
        assertTrue(upNvda.vaultPaused() && upUsdg.vaultPaused(), "new markets start with the vault paused");
    }

    function _batch()
        internal
        view
        returns (address[] memory targets, uint256[] memory values, bytes[] memory payloads)
    {
        StockOracleGuard.FeedConfig memory feed = IOptGuard(GUARD).feedConfig(PROD);
        PoolKey memory key = IOptAdapter(ADAPTER).poolKey(PROD);
        PairConfig memory cfg = PairConfig({
            stockToken: NVDA,
            usdg: USDG,
            stockAccount: address(upNvda),
            usdgAccount: address(upUsdg),
            maxPairValueUSDG: 250e18,
            maxSettlementSwapUSDG: 25e18,
            maxCheckpointAge: 3600,
            deprecatedMinDeadlineDelay: 0,
            maxDeadlineDelay: 300,
            reserveFeeBps: 2000,
            maxSwapSlippageBps: 100,
            withdrawOverUnwindBps: 200,
            stockDecimals: 18,
            usdgDecimals: 6,
            allocationPaused: false,
            swapsPaused: false,
            emergencyMode: false,
            exists: false
        });
        IUniswapV4PairedAdapter.RegisterPairParams memory reg = IUniswapV4PairedAdapter.RegisterPairParams({
            stockToken: NVDA, usdg: USDG, poolKey: key, expectedPoolId: feed.poolId, removalToleranceBps: 400
        });
        (bool en, uint16 hw, uint16 trig, uint32 minInt, uint8 perDay, uint16 lossBps,) = vault.rangePolicy(PROD);
        RangePolicy memory policy = RangePolicy({
            enabled: en,
            halfWidthTicks: hw,
            triggerTicks: trig,
            minInterval: minInt,
            maxPerDay: perDay,
            maxLossBps: lossBps,
            maxRangedValueUsd: 50e18
        });
        StrategyLossReserve.ReserveConfig memory rc = StrategyLossReserve.ReserveConfig({
            stockToken: NVDA,
            usdg: USDG,
            maxUsePerTxUSDG: 10e18,
            dailyCapUSDG: 25e18,
            maxCoverageBps: 5000,
            paused: false,
            exists: false
        });
        targets = new address[](4);
        values = new uint256[](4);
        payloads = new bytes[](4);
        targets[0] = GUARD;
        payloads[0] = abi.encodeCall(IOptGuard.configurePair, (OPTIN, feed));
        targets[1] = RESERVE;
        payloads[1] = abi.encodeCall(IOptReserve.configurePair, (OPTIN, rc));
        targets[2] = VAULT;
        payloads[2] = abi.encodeCall(IOptVault.registerPair, (OPTIN, cfg, reg));
        targets[3] = VAULT;
        payloads[3] = abi.encodeCall(IOptVault.setRangePolicy, (OPTIN, policy));
    }

    function _t0() internal {
        (address[] memory t, uint256[] memory v, bytes[] memory p) = _batch();
        vm.startPrank(LEDGER);
        TimelockController(payable(TIMELOCK)).scheduleBatch(t, v, p, bytes32(0), SALT, 3600);
        assertEq(controller._setPriceOracle(address(adapterN)), 0);
        assertEq(controller.oracle(), address(adapterN));
        assertEq(controller._supportMarket(address(upNvda)), 0);
        assertEq(controller._supportMarket(address(upUsdg)), 0);
        controller._setBorrowPaused(address(upNvda), true);
        controller._setBorrowPaused(address(upUsdg), true);
        controller._setMintPaused(address(upNvda), true);
        controller._setMintPaused(address(upUsdg), true);
        upNvda.queueSetVaultConfig(VAULT, OPTIN, 0.5e18, LEDGER);
        upUsdg.queueSetVaultConfig(VAULT, OPTIN, 0.5e18, LEDGER);
        upNvda.queueSetVaultPaused(false);
        upUsdg.queueSetVaultPaused(false);
        IOptMarket(PNVDA).queueSetVaultPaused(true); // O1
        IOptMarket(PUSDG).queueSetVaultPaused(true); // O2
        vm.stopPrank();
    }

    function _t1() internal {
        (address[] memory t, uint256[] memory v, bytes[] memory p) = _batch();
        vm.startPrank(LEDGER);
        TimelockController(payable(TIMELOCK)).executeBatch(t, v, p, bytes32(0), SALT);
        upNvda._setVaultConfig(VAULT, OPTIN, 0.5e18, LEDGER);
        upUsdg._setVaultConfig(VAULT, OPTIN, 0.5e18, LEDGER);
        upNvda._setVaultPaused(false);
        upUsdg._setVaultPaused(false);
        IOptERC20(NVDA).approve(RESERVE, RESERVE_NVDA);
        IOptReserve(RESERVE).deposit(OPTIN, NVDA, RESERVE_NVDA);
        IOptERC20(USDG).approve(RESERVE, RESERVE_USDG);
        IOptReserve(RESERVE).deposit(OPTIN, USDG, RESERVE_USDG);
        IOptERC20(NVDA).approve(address(upNvda), SEED_NVDA);
        IOptERC20(USDG).approve(address(upUsdg), SEED_USDG);
        controller._setMintPaused(address(upNvda), false);
        require(upNvda.totalSupply() == 0, "SEED_FRONT_RUN");
        assertEq(upNvda.mint(SEED_NVDA), 0);
        controller._setMintPaused(address(upUsdg), false);
        require(upUsdg.totalSupply() == 0, "SEED_FRONT_RUN");
        assertEq(upUsdg.mint(SEED_USDG), 0);
        upNvda.transfer(DEAD, upNvda.balanceOf(LEDGER));
        upUsdg.transfer(DEAD, upUsdg.balanceOf(LEDGER));
        IOptMarket(PNVDA)._setVaultPaused(true); // O3
        IOptMarket(PUSDG)._setVaultPaused(true); // O4
        vm.stopPrank();
    }

    /// NVDA/USDG lending prices, the pSPY price, the production pair's position and active margin positions' metrics.
    function _untouched() internal view returns (bytes memory out) {
        address o = controller.oracle();
        out = abi.encode(
            IOptPriceOracle(o).getUnderlyingPrice(PNVDA),
            IOptPriceOracle(o).getUnderlyingPrice(PUSDG),
            postSpy ? IOptPriceOracle(o).getUnderlyingPrice(PSPY) : 0,
            IOptAdapter(ADAPTER).positionState(PROD).tokenId,
            IOptAdapter(ADAPTER).positionState(PROD).liquidity
        );
        (bool ok, bytes memory n) = EXECUTOR.staticcall(abi.encodeWithSignature("nextPositionId()"));
        if (!ok) return out;
        uint256 next = abi.decode(n, (uint256));
        for (uint256 i = 1; i < next; i++) {
            (bool okP, bytes memory pos) = EXECUTOR.staticcall(abi.encodeWithSignature("positions(uint256)", i));
            if (!okP || pos.length < 12 * 32) continue;
            (,, address account,,,,,,,,, uint8 status) = abi.decode(
                pos,
                (uint256, address, address, address, address, address, uint256, uint256, uint256, uint16, uint8, uint8)
            );
            if (status != 2) continue;
            (, bytes memory m) = RISK.staticcall(abi.encodeWithSignature("getMetrics(address)", account));
            out = abi.encodePacked(out, i, m);
        }
    }

    function _deadline() internal view returns (uint256) {
        return block.timestamp + 200;
    }

    function _keeperCycle() internal {
        vm.startPrank(RANGE_KEEPER);
        vault.checkpoint(OPTIN, _deadline());
        vault.rebalance(OPTIN, _deadline());
        vm.stopPrank();
    }

    function _supply(IOptMarket market, address token, address who, uint256 amount) internal {
        vm.prank(WHALE);
        IOptERC20(token).transfer(who, amount);
        vm.startPrank(who);
        IOptERC20(token).approve(address(market), amount);
        assertEq(market.mint(amount), 0);
        vm.stopPrank();
    }

    function _assertProductionUntouched() internal view {
        PairLedger memory l = vault.ledger(PROD);
        assertEq(l.cumulativeLossUSDG, prodLedgerBefore.cumulativeLossUSDG, "production loss unchanged");
        assertEq(IOptAdapter(ADAPTER).positionState(PROD).liquidity, 0, "production pair still has no liquidity");
        PairConfig memory c = vault.pairConfig(PROD);
        assertTrue(c.allocationPaused && c.swapsPaused && !c.emergencyMode, "production flags stay 110");
        assertEq(IOptReserve(RESERVE).available(PROD, NVDA), prodReserveNvdaBefore, "production reserve unchanged");
    }

    function _shock(int256 bps) internal {
        (, int256 live,,,) = FreshFeedMockLike(NVDA_FEED).latestRoundData();
        vm.etch(NVDA_FEED, type(FreshFeedMock).runtimeCode);
        vm.store(NVDA_FEED, bytes32(0), bytes32(uint256(live * (10_000 + bps) / 10_000)));
    }

    // ---------------------------------------------------------------- tests

    function testCeremonyEndState() public {
        assertEq(untouchedAfterT0, untouchedBefore, "oracle switch + listing leave NVDA/USDG/SPY prices and margin unchanged");
        assertEq(controller.oracle(), address(adapterN));
        (bool listedN, uint256 cfN,) = controller.markets(address(upNvda));
        (bool listedU, uint256 cfU,) = controller.markets(address(upUsdg));
        assertTrue(listedN && listedU);
        assertEq(cfN + cfU, 0, "collateral factor 0");
        assertTrue(controller.borrowGuardianPaused(address(upNvda)) && controller.borrowGuardianPaused(address(upUsdg)));
        assertFalse(controller.mintGuardianPaused(address(upNvda)) || controller.mintGuardianPaused(address(upUsdg)));
        PairConfig memory c = vault.pairConfig(OPTIN);
        assertTrue(c.exists);
        assertEq(c.stockAccount, address(upNvda));
        assertEq(c.usdgAccount, address(upUsdg));
        assertFalse(c.allocationPaused || c.swapsPaused || c.emergencyMode, "flags 000: allocation and swaps open");
        assertEq(c.maxPairValueUSDG, 250e18);
        assertEq(IOptAdapter(ADAPTER).removalTolerance(OPTIN), 400);
        (,,,,,, uint128 ranged) = vault.rangePolicy(OPTIN);
        assertEq(ranged, 50e18);
        assertEq(upNvda.robinhoodPairId(), OPTIN);
        assertFalse(upNvda.vaultPaused() || upUsdg.vaultPaused());
        assertEq(IOptReserve(RESERVE).available(OPTIN, NVDA), RESERVE_NVDA);
        assertEq(IOptReserve(RESERVE).available(OPTIN, USDG), RESERVE_USDG);
        // Seeds at the dead address; half of each seed went to the vault (buffer 0.5).
        assertEq(upNvda.balanceOf(DEAD), upNvda.totalSupply());
        assertEq(upUsdg.balanceOf(DEAD), upUsdg.totalSupply());
        PairLedger memory l = vault.ledger(OPTIN);
        assertEq(l.stockPrincipal, SEED_NVDA / 2);
        assertEq(l.usdgPrincipal, SEED_USDG / 2);
        // O3/O4: plain markets no longer use the vault and their dust came home.
        assertTrue(IOptMarket(PNVDA).vaultPaused() && IOptMarket(PUSDG).vaultPaused());
        assertEq(IOptMarket(PNVDA).vaultAccountedAssets() + IOptMarket(PUSDG).vaultAccountedAssets(), 0);
        PairLedger memory p = vault.ledger(PROD);
        assertEq(p.stockPrincipal + p.usdgPrincipal, 0, "production principals withdrawn to the plain markets");
        _assertProductionUntouched();
        emit log_named_string("borrowAccountingEnabled upNVDA", upNvda.borrowAccountingEnabled() ? "true" : "false");
    }

    function testEnteringOptInMarketsDoesNotFreezeAnExistingBorrower() public {
        (uint256 e0, uint256 liq0, uint256 sf0) = controller.getAccountLiquidity(NVDA_LENDER);
        address[] memory ms = new address[](2);
        ms[0] = address(upNvda);
        ms[1] = address(upUsdg);
        vm.prank(NVDA_LENDER);
        controller.enterMarkets(ms);
        (uint256 e1, uint256 liq1, uint256 sf1) = controller.getAccountLiquidity(NVDA_LENDER);
        assertEq(e1, e0);
        assertEq(liq1, liq0);
        assertEq(sf1, sf0);
    }

    function testOptInMarketsGiveNoBorrowPowerAndCannotBeBorrowed() public {
        address user = makeAddr("optin-user");
        _supply(upUsdg, USDG, user, 20e6);
        address[] memory ms = new address[](1);
        ms[0] = address(upUsdg);
        vm.startPrank(user);
        controller.enterMarkets(ms);
        (uint256 err, uint256 liq,) = controller.getAccountLiquidity(user);
        assertEq(err, 0);
        assertEq(liq, 0, "CF 0: no borrowing power");
        vm.expectRevert(bytes("borrow is paused"));
        upUsdg.borrow(1e6);
        vm.stopPrank();
        assertEq(upUsdg.totalBorrows(), 0);
    }

    function testLifecycleDepositDeployShockAndNativeExits() public {
        address alice = makeAddr("nvda-supplier");
        address bob = makeAddr("usdg-supplier");
        _supply(upNvda, NVDA, alice, 0.5e18); // about $73
        _supply(upUsdg, USDG, bob, 73e6);
        _keeperCycle();
        IUniswapV4PairedAdapter.PositionState memory pos = IOptAdapter(ADAPTER).positionState(OPTIN);
        assertGt(pos.liquidity, 0, "opt-in liquidity deployed");
        assertTrue(pos.tokenId != 0 && pos.tokenId != IOptAdapter(ADAPTER).positionState(PROD).tokenId, "own position NFT");
        _assertProductionUntouched();

        _shock(-200); // NVDA -2 % at the oracle; the pool stays (inside the 300 bp allocation bound)
        vm.prank(RANGE_KEEPER);
        vault.checkpoint(OPTIN, _deadline());
        PairLedger memory afterShock = vault.ledger(OPTIN);
        emit log_named_uint("cumulativeLossUSDG after -2%", afterShock.cumulativeLossUSDG);

        // Both sides leave in their own token through the guarded settle path (LP unwind, reserve, bounded swap).
        uint256 usdgBefore = IOptERC20(USDG).balanceOf(bob);
        uint256 bobShares = upUsdg.balanceOf(bob);
        uint256 quote = bobShares * upUsdg.exchangeRateCurrent() / 1e18;
        vm.prank(bob);
        assertEq(upUsdg.redeem(bobShares), 0);
        uint256 bobPaid = IOptERC20(USDG).balanceOf(bob) - usdgBefore;
        emit log_named_uint("USDG supplier paid (raw)", bobPaid);
        assertGe(bobPaid, quote * 99 / 100, "USDG side exits in USDG");

        uint256 nvdaBefore = IOptERC20(NVDA).balanceOf(alice);
        uint256 aliceShares = upNvda.balanceOf(alice);
        vm.prank(alice);
        assertEq(upNvda.redeem(aliceShares), 0);
        uint256 alicePaid = IOptERC20(NVDA).balanceOf(alice) - nvdaBefore;
        emit log_named_uint("NVDA supplier paid (raw)", alicePaid);
        assertGt(alicePaid, 0.48e18, "NVDA side exits in NVDA");
        _assertProductionUntouched();
    }

    function testDepositAbovePairCapStaysAsCashInTheMarket() public {
        address whaleUser = makeAddr("big-usdg");
        PairLedger memory before = vault.ledger(OPTIN);
        vm.recordLogs();
        _supply(upUsdg, USDG, whaleUser, 600e6); // half (300 USDG) would exceed the $250 pair cap
        Vm.Log[] memory logs = vm.getRecordedLogs();
        bool failed;
        for (uint256 i; i < logs.length; i++) {
            if (logs[i].emitter == address(upUsdg) && logs[i].topics[0] == DEPOSIT_FAILED) failed = true;
        }
        assertTrue(failed, "VaultDepositFailed: the cap refuses the deposit, the mint still succeeds");
        assertEq(vault.ledger(OPTIN).usdgPrincipal, before.usdgPrincipal, "no LP exposure added");
        assertGe(upUsdg.getCash(), 600e6, "cash stays in the market");
    }

    function testStaleFeedLeavesOnlyLocalCashRedeemable() public {
        address carol = makeAddr("usdg-stale");
        _supply(upNvda, NVDA, makeAddr("nvda-stale"), 0.2e18);
        _supply(upUsdg, USDG, carol, 30e6);
        _keeperCycle();
        assertGt(upUsdg.vaultWithdrawableAssets(), 0);
        // Past the guard's 25 h staleness bound (mocked, so it also holds where the feed is etched fresh).
        (, int256 live,,,) = FreshFeedMockLike(NVDA_FEED).latestRoundData();
        uint256 old = block.timestamp - 90_001;
        vm.mockCall(
            NVDA_FEED,
            abi.encodeWithSignature("latestRoundData()"),
            abi.encode(uint80(1), live, old, old, uint80(1))
        );
        assertEq(upUsdg.vaultWithdrawableAssets(), 0, "nothing reachable in the vault while stale");
        uint256 cash = upUsdg.getCash();
        uint256 shares = upUsdg.balanceOf(carol);
        vm.startPrank(carol);
        // Redeeming everything needs the vault: refused. A slice inside local cash still pays.
        vm.expectRevert();
        upUsdg.redeem(shares);
        uint256 slice = shares / 10;
        assertLe(slice * upUsdg.exchangeRateStored() / 1e18, cash);
        assertEq(upUsdg.redeem(slice), 0);
        vm.stopPrank();
    }

    function testPlainMarketsNoLongerTouchTheVault() public {
        address dave = makeAddr("plain-supplier");
        vm.recordLogs();
        _supply(IOptMarket(PUSDG), USDG, dave, 5e6);
        Vm.Log[] memory logs = vm.getRecordedLogs();
        for (uint256 i; i < logs.length; i++) {
            assertTrue(logs[i].topics.length == 0 || logs[i].topics[0] != DEPOSIT_FAILED, "no vault attempt");
        }
        assertEq(vault.ledger(PROD).usdgPrincipal, 0);
        assertEq(IOptMarket(PUSDG).vaultAccountedAssets(), 0);
    }

    function testGuardianPauseStopsNewExposure() public {
        vm.prank(LEDGER);
        vault.setPairPause(OPTIN, true, true, false);
        _supply(upUsdg, USDG, makeAddr("after-pause"), 10e6); // deposit attempt is refused and swallowed
        vm.prank(RANGE_KEEPER);
        vm.expectRevert();
        vault.rebalance(OPTIN, _deadline());
    }
}

interface FreshFeedMockLike {
    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80);
}
