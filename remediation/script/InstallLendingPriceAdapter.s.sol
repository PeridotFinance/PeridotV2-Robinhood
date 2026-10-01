// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Script } from "forge-std/Script.sol";
import { console2 } from "forge-std/console2.sol";
import { RobinhoodLendingPriceAdapter } from "../src/RobinhoodLendingPriceAdapter.sol";

interface IInstallController {
    function admin() external view returns (address);
    function oracle() external view returns (address);
    function _setPriceOracle(address) external returns (uint256);
    function borrowGuardianPaused(address) external view returns (bool);
    function seizeGuardianPaused() external view returns (bool);
}

interface IInstallMarket {
    function totalBorrows() external view returns (uint256);
}

/// @notice User-signed deployment and oracle switch. Never unpauses markets.
contract InstallLendingPriceAdapter is Script {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant CONTROLLER = 0x6148183676E304dbe63a85C350c208DA3cEAc39C;
    address constant SOURCE = 0x266F014d1325774F1190f963Df4369E07dDA1d33;
    address constant PUSDG = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    address constant PSTOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;

    function run() external {
        require(block.chainid == 4663, "WRONG_CHAIN");
        IInstallController controller = IInstallController(CONTROLLER);
        require(controller.admin() == GOVERNOR, "ADMIN_CHANGED");
        require(controller.oracle() == SOURCE, "ORACLE_ALREADY_CHANGED");
        require(
            controller.borrowGuardianPaused(PUSDG) && controller.borrowGuardianPaused(PSTOCK),
            "PAUSE_BORROWS_FIRST"
        );
        require(controller.seizeGuardianPaused(), "PAUSE_SEIZE_FIRST");
        require(
            IInstallMarket(PUSDG).totalBorrows() == 0 && IInstallMarket(PSTOCK).totalBorrows() == 0,
            "REVIEW_EXISTING_DEBT"
        );
        vm.startBroadcast(GOVERNOR);
        RobinhoodLendingPriceAdapter adapter =
            new RobinhoodLendingPriceAdapter(SOURCE, PSTOCK, PUSDG);
        require(controller._setPriceOracle(address(adapter)) == 0, "SET_ORACLE_FAILED");
        vm.stopBroadcast();
        require(controller.oracle() == address(adapter), "ORACLE_NOT_APPLIED");
        require(
            adapter.getUnderlyingPrice(PUSDG) == adapter.assetPrices(adapter.dollar()) * 1e12,
            "BAD_DOLLAR_SCALE"
        );
        require(
            adapter.getUnderlyingPrice(PSTOCK) == adapter.assetPrices(adapter.stock()),
            "BAD_STOCK_SCALE"
        );
        require(
            controller.borrowGuardianPaused(PUSDG) && controller.borrowGuardianPaused(PSTOCK)
                && controller.seizeGuardianPaused(),
            "PAUSES_CHANGED"
        );
        console2.log("Lending adapter:", address(adapter));
        console2.log(
            "Borrows and ordinary seizure remain paused. Independently verify before reactivation."
        );
    }
}
