// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import {Script} from "forge-std/Script.sol";
import {console2} from "forge-std/console2.sol";
import {TimelockController} from "@openzeppelin/contracts/governance/TimelockController.sol";

/// @dev Called ONLY by the localhost-checked rehearsal runner with an unlocked anvil account.
contract DeployLocalRamsesTimelock is Script {
    function run() external {
        require(block.chainid == 4663 && vm.envBool("RAMSES_LOCAL_REHEARSAL"), "LOCAL_ONLY");
        address sender = vm.envAddress("RAMSES_PUBLIC_SENDER");
        address[] memory accounts = new address[](1); accounts[0] = sender;
        vm.startBroadcast(sender);
        TimelockController t = new TimelockController(1 hours, accounts, accounts, address(0));
        vm.stopBroadcast();
        console2.log("LOCAL_TIMELOCK", address(t));
    }
}
