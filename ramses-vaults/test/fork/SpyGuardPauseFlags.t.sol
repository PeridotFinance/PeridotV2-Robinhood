// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { Test } from "forge-std/Test.sol";
import { RamsesOracleGuard } from "../../src/RamsesOracleGuard.sol";
import { IRamsesPool, IRamsesGauge } from "../../src/interfaces/IRamsesV3.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";

/// @notice The SPY/USDG guard, configured exactly as DeployRamsesInKind configures it, against the REAL SPY token, RHSPY
/// feed and Ramses pool. Shows the guard refuses on the token's own pause flags, not only on the feed's age, and that every
/// allocation and exit validation goes through the same check.
contract SpyGuardPauseFlagsForkTest is Test {
    bytes32 constant PAIR = keccak256("SPY/USDG");
    address constant SPY = 0x117cc2133c37B721F49dE2A7a74833232B3B4C0C;
    address constant USDG = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    IRamsesPool constant POOL = IRamsesPool(0x38453c115607463Ac284820Ce959831042f3Df4E);
    IRamsesGauge constant GAUGE = IRamsesGauge(0x739E31798EFE8Cff3ef4c613D8ae751e179C7337);
    IAggregatorV3 constant FEED = IAggregatorV3(0x319724394D3A0e3669269846abE664Cd621f9f6A);
    RamsesOracleGuard guard;

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        guard = new RamsesOracleGuard(address(this));
        guard.configurePair(
            PAIR,
            RamsesOracleGuard.FeedConfig(
                SPY, USDG, FEED, POOL, GAUGE, 12 hours, 300, 600, 18, 6, 8, true
            )
        );
        // Freshen the real answer so the baseline passes regardless of the hour this runs.
        (, int256 answer,,,) = FEED.latestRoundData();
        uint256 t = block.timestamp;
        vm.mockCall(
            address(FEED),
            abi.encodeWithSignature("latestRoundData()"),
            abi.encode(uint80(9), answer, t, t, uint80(9))
        );
    }

    function _allRefuse(bytes4 expected) internal {
        vm.expectRevert(expected);
        guard.pricesUSD18(PAIR);
        vm.expectRevert(expected);
        guard.validatePoolPrice(PAIR, POOL); // allocation path
        vm.expectRevert(expected);
        guard.validateRemovalPrice(PAIR, POOL); // exit path
        vm.expectRevert(expected);
        guard.referenceSqrtPriceX96(PAIR, true);
    }

    function testBaselinePricesWithBothFlagsFalse() public view {
        (bool p,) = SPY.staticcall(abi.encodeWithSignature("paused()"));
        assertTrue(p, "the real SPY token answers paused()");
        (uint256 price,) = guard.pricesUSD18(PAIR);
        assertGt(price, 100e18, "SPY above $100");
    }

    function testTokenPauseFlagRefusesEveryPath() public {
        vm.mockCall(SPY, abi.encodeWithSignature("paused()"), abi.encode(true));
        _allRefuse(RamsesOracleGuard.StockOraclePaused.selector);
    }

    function testTokenOraclePausedFlagRefusesEveryPath() public {
        vm.mockCall(SPY, abi.encodeWithSignature("oraclePaused()"), abi.encode(true));
        _allRefuse(RamsesOracleGuard.StockOraclePaused.selector);
    }

    function testFlagsAreCheckedEvenWithAFreshFeed() public {
        // The feed is fresh (stamped in setUp); only the flag changes, so age alone cannot be what refuses.
        vm.mockCall(SPY, abi.encodeWithSignature("oraclePaused()"), abi.encode(true));
        vm.expectRevert(RamsesOracleGuard.StockOraclePaused.selector);
        guard.pricesUSD18(PAIR);
        vm.mockCall(SPY, abi.encodeWithSignature("oraclePaused()"), abi.encode(false));
        (uint256 price,) = guard.pricesUSD18(PAIR);
        assertGt(price, 0);
    }

    function testStaleFeedAlsoRefusesEveryPath() public {
        uint256 t = block.timestamp;
        vm.mockCall(
            address(FEED),
            abi.encodeWithSignature("latestRoundData()"),
            abi.encode(uint80(9), int256(7e10), t - 12 hours - 1, t - 12 hours - 1, uint80(9))
        );
        vm.expectRevert(
            abi.encodeWithSelector(
                RamsesOracleGuard.StaleOracle.selector, address(FEED), t - 12 hours - 1
            )
        );
        guard.pricesUSD18(PAIR);
    }

    function testIncompleteRoundAndBadAnswerRefuse() public {
        uint256 t = block.timestamp;
        vm.mockCall(
            address(FEED),
            abi.encodeWithSignature("latestRoundData()"),
            abi.encode(uint80(9), int256(7e10), t, t, uint80(8))
        );
        vm.expectRevert(
            abi.encodeWithSelector(
                RamsesOracleGuard.IncompleteOracleRound.selector,
                address(FEED),
                uint80(9),
                uint80(8)
            )
        );
        guard.pricesUSD18(PAIR);
        vm.mockCall(
            address(FEED),
            abi.encodeWithSignature("latestRoundData()"),
            abi.encode(uint80(9), int256(0), t, t, uint80(9))
        );
        vm.expectRevert(
            abi.encodeWithSelector(RamsesOracleGuard.InvalidOracleAnswer.selector, address(FEED))
        );
        guard.pricesUSD18(PAIR);
    }
}
