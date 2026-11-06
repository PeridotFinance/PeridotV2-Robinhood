// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { SafeERC20 } from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { RamsesBoostedVault } from "../../src/RamsesBoostedVault.sol";
import { RamsesOracleGuard } from "../../src/RamsesOracleGuard.sol";
import { RamsesVaultAdapter } from "../../src/RamsesVaultAdapter.sol";
import { IRamsesPool } from "../../src/interfaces/IRamsesV3.sol";
import { CoverageGuard } from "./CoverageGuard.sol";

/// @notice PHASE 0 PROTOTYPE, TEST-ONLY. Runs the UNMODIFIED `RamsesBoostedVault` code behind a router (delegatecall, same storage) and adds exactly two things
/// around it. The vault has about 100 bytes of EIP-170 headroom, so a production version would also need an external library or extension, not more inline code.
///   1. ATOMIC COVERAGE CHECK. `rebalance` and `recenter` run first, then revert if the ranged exposure INCREASED and the reserve no longer covers it;
///      `depositForPair` and any UNPAUSING `setPairPause` require coverage outright. A failed read (stale price, failed reserve call) reverts the whole
///      transaction, so it can never admit new exposure. Everything else (checkpoint, withdrawals, emergency decrease, a recentre that does not raise
///      exposure, pausing) passes untouched, subject only to the vault's own checks.
///   2. `exchangeSurplus` through `InKindExtension`.
/// RECOVERY PATH: the guard and the guardian cannot unpause. Resuming needs CONFIG_ROLE (the timelock) AND passes through the same atomic check, so an
/// authorized resume still fails while the reserve is short or the price is stale. Changing the coverage requirement would be a separate timelocked action.
contract RoutedVault {
    address public immutable implementation;
    address public immutable extension;
    address private immutable _owner;
    // Far from the vault's linear and namespaced storage.
    bytes32 private constant GATE_SLOT = keccak256("peridot.prototype.routedvault.gate");

    constructor(address implementation_, address extension_) {
        implementation = implementation_;
        extension = extension_;
        _owner = msg.sender;
    }

    function setGate(CoverageGuard g) external {
        require(msg.sender == _owner, "OWNER");
        bytes32 slot = GATE_SLOT;
        assembly {
            sstore(slot, g)
        }
    }

    function gate() public view returns (CoverageGuard g) {
        bytes32 slot = GATE_SLOT;
        assembly {
            g := sload(slot)
        }
    }

    function _delegate(address target) private returns (bytes memory ret) {
        bool ok;
        (ok, ret) = target.delegatecall(msg.data);
        if (!ok) {
            assembly {
                revert(add(ret, 32), mload(ret))
            }
        }
    }

    fallback() external payable {
        bytes4 sel = msg.sig;
        CoverageGuard g = gate();
        bytes memory ret;
        if (sel == InKindExtension.exchangeSurplus.selector || sel == InKindExtension.inKindCheckpoint.selector || sel == InKindExtension.setInKindLimit.selector || sel == InKindExtension.inKindStats.selector) {
            ret = _delegate(extension);
        } else if (address(g) != address(0) && (sel == RamsesBoostedVault.rebalance.selector || sel == RamsesBoostedVault.recenter.selector)) {
            // If the exposure cannot be read beforehand the vault's own price checks reject the operation too; count it as zero so any exposure after is new.
            uint256 before_;
            try g.snapshot() returns (uint256 e, uint256, uint256) {
                before_ = e;
            } catch { }
            ret = _delegate(implementation);
            (uint256 after_,,) = g.snapshot();
            if (after_ > before_) g.requireCovered();
        } else if (address(g) != address(0) && sel == RamsesBoostedVault.depositForPair.selector) {
            g.requireCovered();
            ret = _delegate(implementation);
        } else if (address(g) != address(0) && sel == RamsesBoostedVault.setPairPause.selector) {
            (, bool allocationPaused, bool swapsPaused,) = abi.decode(msg.data[4:], (bytes32, bool, bool, bool));
            ret = _delegate(implementation);
            if (!allocationPaused || !swapsPaused) g.requireCovered();
        } else {
            ret = _delegate(implementation);
        }
        assembly {
            return(add(ret, 32), mload(ret))
        }
    }

    receive() external payable { }
}

/// @notice Runs by delegatecall in the vault's storage. Exchanges the SURPLUS of one token for the DEFICIT of the other at the FRESH oracle price, moving real
/// tokens. Principals do not change and the value at the oracle price is conserved (rounding favours the vault), so no holder is charged. No reserve is used.
///   * REENTRANCY: holds the vault's own OpenZeppelin guard (namespaced slot) across the transfers, so no vault entry point can run inside the exchange.
///   * AUTHORIZATION: `IN_KIND_OPERATOR_ROLE` reaches only `exchangeSurplus` and `inKindCheckpoint`. The operator holds no KEEPER_ROLE. `inKindCheckpoint` makes a self-call, so
///     the VAULT holds KEEPER_ROLE on itself; this extension's checkpoint is the only path that uses it.
///   * ACCUMULATION: a rolling UTC-day value limit and lifetime totals per pair. Limit 0 refuses everything; only CONFIG_ROLE sets it. Many small exchanges cannot pass the limit or go unrecorded.
contract InKindExtension {
    using SafeERC20 for IERC20;

    bytes32 public constant IN_KIND_OPERATOR_ROLE = keccak256("IN_KIND_OPERATOR_ROLE");
    bytes32 private constant CONFIG_ROLE = keccak256("CONFIG_ROLE");
    uint256 private constant LEDGER_SLOT = 4; // RamsesBoostedVault._ledger; asserted against `ledger()` by the tests
    bytes32 private constant GUARD_SLOT = 0x9b779b17422d0df92223018b32b4d1fa46e071723d6817e2486d003becc55f00; // ReentrancyGuardUpgradeable
    bytes32 private constant STATS_SLOT = keccak256("peridot.prototype.inkind.stats");

    struct Stats {
        uint256 limitUsdPerDay; // USD 1e18
        uint64 day;
        uint192 usedTodayUsd;
        uint256 calls;
        uint256 lifetimeUsd;
        uint256 lifetimeStockIn;
        uint256 lifetimeUsdgIn;
        uint256 lifetimeStockOut;
        uint256 lifetimeUsdgOut;
    }

    error NotOperator();
    error NotConfig();
    error Reentrant();
    error ExceedsDeficit(uint256 amountIn, uint256 deficit);
    error ExceedsSurplus(uint256 amountOut, uint256 surplus);
    error ExceedsIdle(uint256 amountOut, uint256 idle);
    error InKindLimitExceeded(uint256 used, uint256 limit);

    event SurplusExchanged(bytes32 indexed pairId, address indexed tokenIn, uint256 amountIn, uint256 amountOut, uint256 stockPrice, uint256 usedTodayUsd, uint256 lifetimeUsd);
    event InKindLimitSet(bytes32 indexed pairId, uint256 limitUsdPerDay);

    function _stats(bytes32 pairId) private pure returns (Stats storage st) {
        bytes32 slot = keccak256(abi.encode(pairId, STATS_SLOT));
        assembly {
            st.slot := slot
        }
    }

    function inKindStats(bytes32 pairId) external view returns (Stats memory) {
        return _stats(pairId);
    }

    function setInKindLimit(bytes32 pairId, uint256 limitUsdPerDay) external {
        if (!RamsesBoostedVault(payable(address(this))).hasRole(CONFIG_ROLE, msg.sender)) revert NotConfig();
        _stats(pairId).limitUsdPerDay = limitUsdPerDay;
        emit InKindLimitSet(pairId, limitUsdPerDay);
    }

    /// @notice Recognize any loss first, without giving the operator the keeper's powers.
    function inKindCheckpoint(bytes32 pairId, uint256 deadline) external {
        RamsesBoostedVault v = RamsesBoostedVault(payable(address(this)));
        if (!v.hasRole(IN_KIND_OPERATOR_ROLE, msg.sender)) revert NotOperator();
        v.checkpoint(pairId, deadline);
    }

    /// @param scarceIsUsdg true: USDG comes in, stock goes out. false: stock comes in, USDG goes out.
    function exchangeSurplus(bytes32 pairId, bool scarceIsUsdg, uint256 amountIn) external returns (uint256 amountOut) {
        bytes32 guardSlot = GUARD_SLOT;
        uint256 status;
        assembly {
            status := sload(guardSlot)
        }
        if (status == 2) revert Reentrant();
        assembly {
            sstore(guardSlot, 2)
        }
        amountOut = _exchange(pairId, scarceIsUsdg, amountIn);
        assembly {
            sstore(guardSlot, 1)
        }
    }

    function _exchange(bytes32 pairId, bool scarceIsUsdg, uint256 amountIn) private returns (uint256 amountOut) {
        RamsesBoostedVault v = RamsesBoostedVault(payable(address(this)));
        if (!v.hasRole(IN_KIND_OPERATOR_ROLE, msg.sender)) revert NotOperator();
        PairConfig memory c = v.pairConfig(pairId);
        PairLedger memory l = v.ledger(pairId);
        RamsesVaultAdapter adapter = v.liquidityAdapter();
        RamsesOracleGuard guard = v.oracleGuard();
        // The existing exit restrictions: fresh feed, pause flags and the removal deviation bound all revert here.
        (,, uint160 ref) = guard.validateRemovalPrice(pairId, IRamsesPool(adapter.poolOf(pairId)));
        (uint256 ps,) = guard.pricesUSD18(pairId);
        RamsesVaultAdapter.PositionState memory p = adapter.positionStateAt(pairId, ref);
        uint256 stockAssets = l.stockIdle + p.stockAmount;
        uint256 usdgAssets = l.usdgIdle + p.usdgAmount;
        uint256 stockUnit = 10 ** c.stockDecimals;
        uint256 usdgUnit = 10 ** c.usdgDecimals;
        address tokenIn;
        address tokenOut;
        uint256 valueUsd;
        if (scarceIsUsdg) {
            uint256 deficit = l.usdgPrincipal > usdgAssets ? l.usdgPrincipal - usdgAssets : 0;
            if (amountIn > deficit) revert ExceedsDeficit(amountIn, deficit);
            valueUsd = Math.mulDiv(amountIn, 1e18, usdgUnit);
            amountOut = Math.mulDiv(valueUsd, stockUnit, ps);
            uint256 surplus = stockAssets > l.stockPrincipal ? stockAssets - l.stockPrincipal : 0;
            if (amountOut > surplus) revert ExceedsSurplus(amountOut, surplus);
            if (amountOut > l.stockIdle) revert ExceedsIdle(amountOut, l.stockIdle);
            (tokenIn, tokenOut) = (c.usdg, c.stockToken);
        } else {
            uint256 deficit = l.stockPrincipal > stockAssets ? l.stockPrincipal - stockAssets : 0;
            if (amountIn > deficit) revert ExceedsDeficit(amountIn, deficit);
            valueUsd = Math.mulDiv(amountIn, ps, stockUnit);
            amountOut = Math.mulDiv(valueUsd, usdgUnit, 1e18);
            uint256 surplus = usdgAssets > l.usdgPrincipal ? usdgAssets - l.usdgPrincipal : 0;
            if (amountOut > surplus) revert ExceedsSurplus(amountOut, surplus);
            if (amountOut > l.usdgIdle) revert ExceedsIdle(amountOut, l.usdgIdle);
            (tokenIn, tokenOut) = (c.stockToken, c.usdg);
        }
        Stats storage st = _stats(pairId);
        uint64 day = uint64(block.timestamp / 1 days);
        uint256 used = (st.day == day ? st.usedTodayUsd : 0) + valueUsd;
        if (used > st.limitUsdPerDay) revert InKindLimitExceeded(used, st.limitUsdPerDay);
        st.day = day;
        st.usedTodayUsd = uint192(used);
        st.calls += 1;
        st.lifetimeUsd += valueUsd;
        if (scarceIsUsdg) {
            st.lifetimeUsdgIn += amountIn;
            st.lifetimeStockOut += amountOut;
        } else {
            st.lifetimeStockIn += amountIn;
            st.lifetimeUsdgOut += amountOut;
        }
        // Ledger first, then the external transfers.
        bytes32 base = keccak256(abi.encode(pairId, LEDGER_SLOT));
        uint256 stockIdleSlot = uint256(base) + 2;
        uint256 usdgIdleSlot = uint256(base) + 3;
        uint256 balanceBefore = IERC20(tokenIn).balanceOf(address(this));
        IERC20(tokenIn).safeTransferFrom(msg.sender, address(this), amountIn);
        require(IERC20(tokenIn).balanceOf(address(this)) == balanceBefore + amountIn, "DELTA");
        uint256 newStockIdle = scarceIsUsdg ? l.stockIdle - amountOut : l.stockIdle + amountIn;
        uint256 newUsdgIdle = scarceIsUsdg ? l.usdgIdle + amountIn : l.usdgIdle - amountOut;
        assembly {
            sstore(stockIdleSlot, newStockIdle)
            sstore(usdgIdleSlot, newUsdgIdle)
        }
        IERC20(tokenOut).safeTransfer(msg.sender, amountOut);
        emit SurplusExchanged(pairId, tokenIn, amountIn, amountOut, ps, used, st.lifetimeUsd);
    }
}
