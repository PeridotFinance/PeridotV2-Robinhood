// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { Test } from "forge-std/Test.sol";
import { DeployRamsesMarkets } from "../script/DeployMarkets.s.sol";
import { RamsesBoostedVault } from "../src/RamsesBoostedVault.sol";
import { RamsesBoostedDelegate } from "../src/lending/RamsesBoostedDelegate.sol";
import { Peridottroller } from "../src/lending/RamsesUnitroller.sol";
import { RamsesOracleGuard } from "../src/RamsesOracleGuard.sol";
import { RamsesLossReserve } from "../src/RamsesLossReserve.sol";

contract PlanRoleDouble {
    function KEEPER_ROLE() external pure returns (bytes32) {
        return keccak256("KEEPER_ROLE");
    }
}

contract PlanHarness is DeployRamsesMarkets {
    function plan() external returns (address[] memory, bytes[] memory) {
        vault = RamsesBoostedVault(address(new PlanRoleDouble()));
        guard = RamsesOracleGuard(address(10));
        reserve = RamsesLossReserve(address(11));
        controller = Peridottroller(address(12));
        sMarket = RamsesBoostedDelegate(address(13));
        uMarket = RamsesBoostedDelegate(address(14));
        stock = address(15);
        usdg = address(16);
        keeper = address(17);
        sCap = 4.35e18;
        uCap = 1000e6;
        sSeed = 0.087e18;
        uSeed = 20e6;
        _configuration();
        return (targets, payloads);
    }
}

contract LaunchPlanTest is Test {
    function testApprovedCFAndCapsInActualTimelockPlan() public {
        vm.setEnv("RAMSES_STOCK_BORROW_CAP", "4350000000000000000");
        vm.setEnv("RAMSES_USDG_BORROW_CAP", "1000000000");
        (address[] memory targets, bytes[] memory data) = new PlanHarness().plan();
        uint256 factors;
        uint256 supplyCaps;
        uint256 borrowsEnabled;
        uint256 seeds;
        uint256 capIndex;
        for (uint256 i; i < data.length; i++) {
            bytes memory d = data[i];
            bytes4 sig;
            assembly { sig := mload(add(d, 32)) }
            if (
                sig
                    == bytes4(
                        keccak256(
                            "registerPair(bytes32,(address,address,address,address,uint128,uint128,uint64,uint32,uint32,uint16,uint16,uint16,uint8,uint8,bool,bool,bool,bool),(address,address,address,address,uint16,uint16))"
                        )
                    )
            ) {
                uint256 checkpointAge;
                assembly { checkpointAge := mload(add(d, 260)) }
                assertEq(checkpointAge, 3600, "live checkpoint age");
            }
            if (sig == bytes4(keccak256("_setCollateralFactor(address,uint256)"))) {
                assertEq(targets[i], address(12));
                assertEq(
                    keccak256(d),
                    keccak256(
                        abi.encodeWithSelector(
                            sig, factors == 0 ? address(13) : address(14), uint256(0.8e18)
                        )
                    )
                );
                assertEq(seeds, 2, "seed before activating collateral");
                factors++;
            }
            if (sig == bytes4(keccak256("setSupplyCap(uint256)"))) {
                assertEq(
                    keccak256(d),
                    keccak256(
                        abi.encodeWithSelector(
                            sig, supplyCaps == 0 ? uint256(4.35e18) : uint256(1000e6)
                        )
                    )
                );
                supplyCaps++;
            }
            if (sig == bytes4(keccak256("_setMarketBorrowCaps(address[],uint256[])"))) {
                address[] memory markets = new address[](2);
                markets[0] = address(13);
                markets[1] = address(14);
                uint256[] memory caps = new uint256[](2);
                caps[0] = 4.35e18;
                caps[1] = 1000e6;
                assertEq(keccak256(d), keccak256(abi.encodeWithSelector(sig, markets, caps)));
                capIndex = i;
            }
            if (sig == bytes4(keccak256("mint(uint256)"))) {
                assertGt(i, capIndex);
                seeds++;
            }
            if (sig == bytes4(keccak256("_setBorrowPaused(address,bool)"))) {
                uint256 enabled;
                assembly { enabled := mload(add(d, 68)) }
                if (enabled == 0) {
                    assertEq(seeds, 2);
                    borrowsEnabled++;
                }
            }
        }
        assertEq(factors, 2);
        assertEq(supplyCaps, 2);
        assertEq(borrowsEnabled, 2);
        assertGt(capIndex, 0);
    }
}
