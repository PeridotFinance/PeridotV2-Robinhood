// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Script } from "forge-std/Script.sol";
import { console2 } from "forge-std/console2.sol";
import { LendingMintRouter } from "../src/LendingMintRouter.sol";

/// @notice Deploys the stateless min-shares router for the two installed markets. The router has no
/// owner and no privileges, so any account may deploy it; the governor does here only to keep one
/// signing identity. Build with FOUNDRY_PROFILE=lending_candidate and verify the result with
/// verify_lending_mint_router.py before the frontend uses it.
contract DeployLendingMintRouter is Script {
    address constant DEPLOYER = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant PSTOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    address constant PUSDG = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;

    function run() external returns (address router) {
        require(block.chainid == 4663, "WRONG_CHAIN");
        require(PSTOCK.code.length > 0 && PUSDG.code.length > 0, "MARKETS_MISSING");
        vm.startBroadcast(DEPLOYER);
        router = address(new LendingMintRouter(PSTOCK, PUSDG));
        vm.stopBroadcast();
        require(LendingMintRouter(router).marketA() == PSTOCK, "MARKET_A");
        require(LendingMintRouter(router).marketB() == PUSDG, "MARKET_B");
        console2.log("router", router);
        console2.logBytes32(router.codehash);
        console2.log(
            "Next: python3 remediation/tools/verify_lending_mint_router.py --router <address>"
        );
    }
}
