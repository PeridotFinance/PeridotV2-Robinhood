// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;
import { Script } from "forge-std/Script.sol";
import { FiveXRiskUpdate } from "./FiveXRiskUpdate.sol";

contract UpdateMainnetFiveX is Script, FiveXRiskUpdate {
    function queue() external {
        vm.startBroadcast(FIVE_X_GOVERNOR);
        _queueFiveX();
        vm.stopBroadcast();
    }

    function applyRisk() external {
        vm.startBroadcast(FIVE_X_GOVERNOR);
        _applyFiveX();
        vm.stopBroadcast();
    }
}
