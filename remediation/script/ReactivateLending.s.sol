// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Script } from "forge-std/Script.sol";
import { RobinhoodLendingPriceAdapter } from "../src/RobinhoodLendingPriceAdapter.sol";

interface IReactivationController {
    function admin() external view returns (address);
    function oracle() external view returns (address);
    function _setBorrowPaused(address, bool) external returns (bool);
    function _setSeizePaused(bool) external returns (bool);
}

interface IReactivationGuard {
    function pricesUSD18(bytes32) external view returns (uint256, uint256);
}

/// @notice Local governor-signing script for later, after fresh guarded prices return.
/// @dev Does not deploy/fund a Safe or transfer authority. Never relaxes oracle thresholds.
contract ReactivateLending is Script {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant CONTROLLER = 0x6148183676E304dbe63a85C350c208DA3cEAc39C;
    address constant ADAPTER = 0xe4e03C2FdaeF915ACe705D106b2660B1E342A2E4;
    address constant GUARD = 0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741;
    bytes32 constant EXPECTED_RUNTIME =
        0x8a44437ef2c35e187c49f54aa92fcc3c51c3a30c1dc795cc713f610eaf353404;

    function run() external {
        require(block.chainid == 4663, "WRONG_CHAIN");
        IReactivationController controller = IReactivationController(CONTROLLER);
        require(controller.admin() == GOVERNOR, "ADMIN_CHANGED_USE_CURRENT_GOVERNANCE");
        require(
            controller.oracle() == ADAPTER && ADAPTER.codehash == EXPECTED_RUNTIME, "ORACLE_CHANGED"
        );
        RobinhoodLendingPriceAdapter adapter = RobinhoodLendingPriceAdapter(ADAPTER);
        // Reverts on stale/paused stock feed. Closed-market staleness is not waived.
        (uint256 stockPrice, uint256 dollarPrice) =
            IReactivationGuard(GUARD).pricesUSD18(keccak256("NVDA/USDG"));
        require(stockPrice != 0 && dollarPrice != 0, "ZERO_PRICE");
        require(
            adapter.getUnderlyingPrice(adapter.stockMarket()) == stockPrice, "STOCK_PRICE_MISMATCH"
        );
        require(
            adapter.getUnderlyingPrice(adapter.dollarMarket()) == dollarPrice * 1e12,
            "DOLLAR_PRICE_MISMATCH"
        );
        vm.startBroadcast(GOVERNOR);
        // Restore correct ordinary liquidation before allowing any new borrowing.
        require(!controller._setSeizePaused(false), "SEIZE_STILL_PAUSED");
        require(
            !controller._setBorrowPaused(adapter.stockMarket(), false), "STOCK_BORROW_STILL_PAUSED"
        );
        require(
            !controller._setBorrowPaused(adapter.dollarMarket(), false),
            "DOLLAR_BORROW_STILL_PAUSED"
        );
        vm.stopBroadcast();
    }
}
