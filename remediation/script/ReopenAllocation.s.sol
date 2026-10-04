// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Script } from "forge-std/Script.sol";
import { console2 } from "forge-std/console2.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { IUniswapV4PairedAdapter } from "baseline/src/interfaces/IUniswapV4PairedAdapter.sol";

interface IReopenVault {
    function setPairPause(
        bytes32 pairId,
        bool allocationPaused,
        bool swapsPaused,
        bool emergencyMode
    ) external;
    function pairConfig(bytes32 pairId) external view returns (PairConfig memory);
    function ledger(bytes32 pairId) external view returns (PairLedger memory);
    function checkpoint(bytes32 pairId, uint256 deadline) external returns (int256 pnlUSDG);
    function rebalance(bytes32 pairId, uint256 deadline) external;
    function hasRole(bytes32 role, address account) external view returns (bool);
    function KEEPER_ROLE() external view returns (bytes32);
}

interface IReopenGuard {
    function pricesUSD18(bytes32 pairId) external view returns (uint256, uint256);
}

interface IReopenAdapter {
    function positionState(bytes32 pairId)
        external
        view
        returns (IUniswapV4PairedAdapter.PositionState memory);
}

/// @notice Reopens LP allocation for the production NVDA/USDG pair, and nothing else.
/// @dev Three separate stages, each signed locally by the governor:
///      1. QueueReopenAllocation: schedules `setPairPause(pair, false, true, false)` on the timelock.
///         Settlement swaps and emergency mode keep their current (paused / off) values.
///      2. ExecuteReopenAllocation: after the delay, executes it.
///      3. OperateReopenedAllocation: checkpoint, then rebalance, each with an explicit gas limit.
///         The delegate swallows inner failures and eth_estimateGas under-reports, so state is
///         asserted after each call. Checkpoint always precedes rebalance.
/// The guardian cannot unpause, and nothing here widens any bound or touches stale prices: a stale
/// feed makes stage 3 revert and the pair simply stays idle.
abstract contract ReopenBase is Script {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    address constant GUARD = 0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741;
    address constant ADAPTER = 0xadA73211711e4790bc83B5d6B39f47fE04D276f3;
    bytes32 constant PAIR = keccak256("NVDA/USDG");
    bytes32 constant SALT = keccak256("PERIDOT_LP_REOPEN_2026_10_02");
    uint256 constant OPERATOR_GAS = 2_000_000;

    function _payload() internal pure returns (bytes memory) {
        return abi.encodeCall(IReopenVault.setPairPause, (PAIR, false, true, false));
    }

    function _operation() internal view returns (bytes32) {
        return
            TimelockController(payable(TIMELOCK))
                .hashOperation(VAULT, 0, _payload(), bytes32(0), SALT);
    }

    function _requireFreshPrices() internal view {
        // Reverts on a stale or paused stock feed; staleness is never waived.
        (uint256 stockPrice, uint256 usdgPrice) = IReopenGuard(GUARD).pricesUSD18(PAIR);
        require(stockPrice != 0 && usdgPrice != 0, "ZERO_PRICE");
    }
}

contract QueueReopenAllocation is ReopenBase {
    function run() external {
        require(block.chainid == 4663, "WRONG_CHAIN");
        PairConfig memory config = IReopenVault(VAULT).pairConfig(PAIR);
        require(config.exists, "PAIR_MISSING");
        require(config.allocationPaused, "ALLOCATION_ALREADY_OPEN");
        require(config.swapsPaused, "SWAPS_STATE_CHANGED");
        require(!config.emergencyMode, "EMERGENCY_MODE_ON");
        TimelockController timelock = TimelockController(payable(TIMELOCK));
        require(timelock.hasRole(timelock.PROPOSER_ROLE(), GOVERNOR), "PROPOSER_CHANGED");
        require(
            IReopenVault(VAULT).hasRole(IReopenVault(VAULT).KEEPER_ROLE(), GOVERNOR), "NOT_KEEPER"
        );
        uint256 delay = timelock.getMinDelay();
        require(delay >= 1 hours, "TIMELOCK_DELAY_TOO_SHORT");
        _requireFreshPrices();
        bytes32 operation = _operation();
        require(timelock.getTimestamp(operation) == 0, "OPERATION_ALREADY_EXISTS");
        vm.startBroadcast(GOVERNOR);
        timelock.schedule(VAULT, 0, _payload(), bytes32(0), SALT, delay);
        vm.stopBroadcast();
        console2.log("Operation:");
        console2.logBytes32(operation);
        console2.log("Earliest execution timestamp:", timelock.getTimestamp(operation));
        console2.log(
            "Only allocation is reopened. Settlement swaps stay paused; emergency stays off."
        );
    }
}

contract ExecuteReopenAllocation is ReopenBase {
    function run() external {
        require(block.chainid == 4663, "WRONG_CHAIN");
        TimelockController timelock = TimelockController(payable(TIMELOCK));
        bytes32 operation = _operation();
        require(timelock.isOperationReady(operation), "TIMELOCK_NOT_READY");
        bytes32 ledgerBefore = keccak256(abi.encode(IReopenVault(VAULT).ledger(PAIR)));
        vm.startBroadcast(GOVERNOR);
        timelock.execute(VAULT, 0, _payload(), bytes32(0), SALT);
        vm.stopBroadcast();
        require(timelock.isOperationDone(operation), "OPERATION_NOT_DONE");
        PairConfig memory config = IReopenVault(VAULT).pairConfig(PAIR);
        require(!config.allocationPaused, "ALLOCATION_STILL_PAUSED");
        require(config.swapsPaused && !config.emergencyMode, "UNEXPECTED_PAUSE_STATE");
        require(
            keccak256(abi.encode(IReopenVault(VAULT).ledger(PAIR))) == ledgerBefore,
            "LEDGER_CHANGED"
        );
        console2.log("Allocation reopened. Run OperateReopenedAllocation to place the LP position.");
    }
}

contract OperateReopenedAllocation is ReopenBase {
    function run() external {
        require(block.chainid == 4663, "WRONG_CHAIN");
        IReopenVault vault = IReopenVault(VAULT);
        PairConfig memory config = vault.pairConfig(PAIR);
        require(!config.allocationPaused, "ALLOCATION_STILL_PAUSED");
        require(config.swapsPaused && !config.emergencyMode, "UNEXPECTED_PAUSE_STATE");
        require(vault.hasRole(vault.KEEPER_ROLE(), GOVERNOR), "NOT_KEEPER");
        _requireFreshPrices();
        PairLedger memory before = vault.ledger(PAIR);
        uint128 liquidityBefore = IReopenAdapter(ADAPTER).positionState(PAIR).liquidity;

        vm.startBroadcast(GOVERNOR);
        // Checkpoint before rebalance: rebalance reverts CheckpointStale otherwise.
        vault.checkpoint{ gas: OPERATOR_GAS }(PAIR, block.timestamp + 120);
        vault.rebalance{ gas: OPERATOR_GAS }(PAIR, block.timestamp + 120);
        vm.stopBroadcast();

        PairLedger memory afterLedger = vault.ledger(PAIR);
        uint128 liquidityAfter = IReopenAdapter(ADAPTER).positionState(PAIR).liquidity;
        require(liquidityAfter > liquidityBefore, "NO_LIQUIDITY_ADDED");
        // A checkpoint may recognize a loss (principal shrinks) but must never create principal.
        require(
            afterLedger.stockPrincipal <= before.stockPrincipal
                && afterLedger.usdgPrincipal <= before.usdgPrincipal,
            "PRINCIPAL_GREW"
        );
        require(
            afterLedger.stockIdle < before.stockIdle && afterLedger.usdgIdle < before.usdgIdle,
            "IDLE_NOT_DEPLOYED"
        );
        console2.log("LP liquidity:", uint256(liquidityAfter));
        console2.log("stock idle before/after:", before.stockIdle, afterLedger.stockIdle);
        console2.log("usdg idle before/after:", before.usdgIdle, afterLedger.usdgIdle);
    }
}
