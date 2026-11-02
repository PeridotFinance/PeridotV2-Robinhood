// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
import { RamsesOracleGuard } from "../../src/RamsesOracleGuard.sol";
import { IRamsesPool, IRamsesGauge } from "../../src/interfaces/IRamsesV3.sol";

interface IUiStock {
    function uiMultiplier() external view returns (uint256);
    function oraclePaused() external view returns (bool);
    function paused() external view returns (bool);
}

/// @notice Phase 0 proof: SPY pays distributions through `uiMultiplier`. On a fork of the REAL SPY token, SPY/USDG CL1 pool and Chainlink feed:
///   (1) a change of `uiMultiplier` is metadata: balances, total supply and transfers are unchanged, so raw accounting (pool reserves, vault ledgers,
///       adapter balance-delta checks) is not affected by the step itself;
///   (2) what a step CAN do is move the feed's price per raw token away from the pool's spot. The REAL oracle guard (real token, feed mocked fresh, real
///       pool and gauge) is run against steps of -6% .. +6%; it fails closed outside its bounds and does not relax them.
/// The guard bounds used here (150 bp allocation / 300 bp removal) are TEST settings: the Ramses vault is not deployed. Prints machine-readable
/// `GUARD,<step bp>,<allocation 1/0>,<removal 1/0>` lines.
contract StockMultiplierFork is Test {
    address constant SPY = 0x117cc2133c37B721F49dE2A7a74833232B3B4C0C;
    address constant USDG = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address constant FEED = 0x319724394D3A0e3669269846abE664Cd621f9f6A;
    address constant POOL = 0x38453c115607463Ac284820Ce959831042f3Df4E;
    address constant GAUGE = 0x739E31798EFE8Cff3ef4c613D8ae751e179C7337;
    bytes32 constant ID = keccak256("SPY/USDG");

    RamsesOracleGuard guard;

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663, "chain");
        guard = new RamsesOracleGuard(address(this));
        guard.configurePair(
            ID,
            RamsesOracleGuard.FeedConfig(SPY, USDG, IAggregatorV3(FEED), IRamsesPool(POOL), IRamsesGauge(GAUGE), 12 hours, 150, 300, 18, 6, 8, true)
        );
    }

    /// @dev pool spot price, USD per SPY, as the guard computes it (WAD)
    function _spotWad() internal view returns (uint256) {
        (uint160 s,,,,,,) = IRamsesPool(POOL).slot0();
        uint256 ratio = Math.mulDiv(s, s, 1 << 64);                  // token0 = SPY, token1 = USDG: price = s^2 / 2^192 (raw USDG per raw SPY)
        uint256 quoted = Math.mulDiv(ratio, 1e18, 1 << 128);          // USDG raw units per 1 SPY
        return quoted * 1e12;                                         // 6-decimal USDG -> WAD
    }

    function _mockFeed(uint256 answer) internal {
        vm.mockCall(FEED, abi.encodeWithSelector(IAggregatorV3.latestRoundData.selector), abi.encode(uint80(1), int256(answer), block.timestamp, block.timestamp, uint80(1)));
    }

    function testFork_multiplierIsMetadataBalancesAndTransfersUnchanged() public {
        uint256 m0 = IUiStock(SPY).uiMultiplier();
        uint256 poolBalance = IERC20(SPY).balanceOf(POOL);
        uint256 supply = IERC20(SPY).totalSupply();
        (uint160 sqrtBefore,,,,,,) = IRamsesPool(POOL).slot0();
        uint128 liqBefore = IRamsesPool(POOL).liquidity();
        emit log_named_uint("uiMultiplier (1e18)", m0);
        assertGt(m0, 1e18, "the live multiplier has accrued");

        vm.record();
        IUiStock(SPY).uiMultiplier();
        (bytes32[] memory reads,) = vm.accesses(SPY);
        assertGt(reads.length, 0, "uiMultiplier reads token storage");
        bytes32 slot;
        bool found;
        for (uint256 i = 0; i < reads.length; i++) {               // the call also reads proxy/beacon and pause slots: pick the one holding the multiplier
            if (uint256(vm.load(SPY, reads[i])) == m0) {
                slot = reads[i];
                found = true;
                break;
            }
        }
        assertTrue(found, "the multiplier occupies a whole storage slot");
        emit log_named_bytes32("uiMultiplier storage slot", slot);

        uint256 m1 = m0 * 10033 / 10000;                              // a quarterly-size step (about +0.33%): assumed size, not a measured dividend
        vm.store(SPY, slot, bytes32(m1));
        assertEq(IUiStock(SPY).uiMultiplier(), m1, "the multiplier moved");
        assertEq(IERC20(SPY).balanceOf(POOL), poolBalance, "pool balance unchanged by a multiplier step");
        assertEq(IERC20(SPY).totalSupply(), supply, "total supply unchanged");
        (uint160 sqrtAfter,,,,,,) = IRamsesPool(POOL).slot0();
        assertEq(sqrtAfter, sqrtBefore, "pool price is not moved by the token's multiplier");
        assertEq(IRamsesPool(POOL).liquidity(), liqBefore, "pool liquidity unchanged");

        vm.prank(POOL);
        IERC20(SPY).transfer(address(0xBEEF), 1e18);
        assertEq(IERC20(SPY).balanceOf(address(0xBEEF)), 1e18, "transfers move exact raw amounts after the step");
        assertEq(IERC20(SPY).balanceOf(POOL), poolBalance - 1e18);
        emit log_string("RESULT,multiplier is metadata: raw balances, supply, pool price and transfers unchanged");
    }

    function testFork_guardFailsClosedOutsideItsBoundsAndNeverRelaxes() public {
        uint256 spot = _spotWad();
        int256[11] memory steps = [int256(-600), -400, -200, -100, -33, 0, 17, 33, 100, 200, 400];
        for (uint256 i = 0; i < steps.length; i++) {
            uint256 oracle = uint256(int256(spot) + int256(spot) * steps[i] / 10_000);
            _mockFeed(oracle / 1e10);                                  // 8-decimal feed answer
            bool alloc;
            bool removal;
            try guard.validatePoolPrice(ID, IRamsesPool(POOL)) { alloc = true; } catch {}
            try guard.validateRemovalPrice(ID, IRamsesPool(POOL)) { removal = true; } catch {}
            int256 mag = steps[i] < 0 ? -steps[i] : steps[i];
            // allocation bound 150 bp, removal bound 300 bp (test settings); keep a margin for the 1e-10 rounding of the 8-decimal answer
            if (mag <= 140) assertTrue(alloc && removal, "inside both bounds");
            if (mag >= 160 && mag <= 290) assertTrue(!alloc && removal, "between the bounds: allocation fails, exit still works");
            if (mag >= 310) assertTrue(!alloc && !removal, "outside both bounds: both fail closed");
            emit log_string(string.concat("GUARD,", vm.toString(steps[i]), ",", alloc ? "1" : "0", ",", removal ? "1" : "0"));
        }
    }

    function testFork_realFeedAgeAtThePin() public {
        (, int256 answer,, uint256 updated,) = IAggregatorV3(FEED).latestRoundData();
        emit log_named_uint("feed answer (8 decimals)", uint256(answer));
        emit log_named_uint("feed age at the pin, seconds", block.timestamp - updated);
        bool fresh;
        try guard.pricesUSD18(ID) { fresh = true; } catch {}
        emit log_string(string.concat("REALFEED,fresh=", fresh ? "1" : "0"));
    }
}
