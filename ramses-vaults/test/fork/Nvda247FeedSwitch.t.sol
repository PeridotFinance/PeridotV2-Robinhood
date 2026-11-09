// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {Test} from "forge-std/Test.sol";

interface IGuard {
    struct FeedConfig {
        address stockToken;
        address usdg;
        address stockFeed;
        address usdgFeed;
        address sequencerFeed;
        bytes32 poolId;
        uint64 maxStaleness;
        uint64 sequencerGracePeriod;
        uint16 maxPriceDeviationBps;
        uint8 stockDecimals;
        uint8 usdgDecimals;
        uint8 stockFeedDecimals;
        uint8 usdgFeedDecimals;
        bool usdgFixedOne;
        bool enabled;
        uint16 maxRemovalDeviationBps;
    }

    function feedConfig(bytes32) external view returns (FeedConfig memory);
    function configurePair(bytes32, FeedConfig calldata) external;
    function pricesUSD18(bytes32) external view returns (uint256, uint256);
}

interface ILendingOracle {
    function admin(address) external view returns (bool);
    function setAdmin(address) external;
    function registerChainlinkFeed(address, address) external;
    function assetToAggregator(address) external view returns (address);
    function assetPrices(address) external view returns (uint256);
}

interface IPriceView {
    function getPrice(address) external view returns (uint256);
    function getUnderlyingPrice(address) external view returns (uint256);
    function assetPrices(address) external view returns (uint256);
}

interface ITimelock {
    function scheduleBatch(address[] calldata, uint256[] calldata, bytes[] calldata, bytes32, bytes32, uint256) external;
    function executeBatch(address[] calldata, uint256[] calldata, bytes[] calldata, bytes32, bytes32) external payable;
    function cancel(bytes32) external;
    function hashOperationBatch(address[] calldata, uint256[] calldata, bytes[] calldata, bytes32, bytes32)
        external
        pure
        returns (bytes32);
    function isOperationDone(bytes32) external view returns (bool);
}

interface IFeed {
    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80);
}

/// @notice The prepared oracle-source switch from the 24/5 RHNVDA feed to Chainlink "Robinhood NVDA (24/7)", on a fork of
/// the LIVE contracts: Ledger prerequisite (timelock as lending-oracle admin), then one timelock batch (vault guard NVDA
/// config with the 24/7 feed, lending oracle feed for NVDA), then the read-backs of the margin oracle, the guarded margin
/// source, the lending oracle and the pNVDA lending price. Both staleness choices are run. Nothing is sent.
contract Nvda247FeedSwitchForkTest is Test {
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    address constant LEDGER = 0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD;
    address constant GUARD = 0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741;
    address constant LENDING = 0x266F014d1325774F1190f963Df4369E07dDA1d33; // StockSimplePriceOracle
    address constant LENDING_ADAPTER = 0xe4e03C2FdaeF915ACe705D106b2660B1E342A2E4; // the controller's oracle
    address constant MARGIN_ORACLE = 0x63150Eb3DDf71420dA0b09b66838aab398f7dEdD;
    address constant GUARDED_SOURCE = 0x25E02b142E0785a59D85a6CA820c400e601a9E8E;
    address constant PNVDA = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    address constant NVDA = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address constant F5 = 0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15;
    address constant F7 = 0x321A36e441DfBadA01Aa6f9884F14a344E133D1F;
    bytes32 constant PAIR = 0xe2050352f4346597cc69d2776d99ae60c9440dfc28f9406ce66be0bbe3fb6b06;
    bytes32 constant SALT = keccak256("peridot-nvda-247-feed-switch-v1");

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
    }

    function _batch(uint64 maxStaleness)
        internal
        view
        returns (address[] memory t, uint256[] memory v, bytes[] memory p)
    {
        IGuard.FeedConfig memory c = IGuard(GUARD).feedConfig(PAIR);
        c.stockFeed = F7;
        c.maxStaleness = maxStaleness;
        t = new address[](2);
        v = new uint256[](2);
        p = new bytes[](2);
        (t[0], p[0]) = (GUARD, abi.encodeCall(IGuard.configurePair, (PAIR, c)));
        (t[1], p[1]) = (LENDING, abi.encodeCall(ILendingOracle.registerChainlinkFeed, (NVDA, F7)));
    }

    function _switch(uint64 maxStaleness) internal {
        vm.prank(LEDGER);
        ILendingOracle(LENDING).setAdmin(TIMELOCK); // prerequisite: one direct Ledger signature
        (address[] memory t, uint256[] memory v, bytes[] memory p) = _batch(maxStaleness);
        vm.prank(LEDGER);
        ITimelock(TIMELOCK).scheduleBatch(t, v, p, bytes32(0), SALT, 3600);
        vm.warp(block.timestamp + 3600);
        vm.prank(LEDGER);
        ITimelock(TIMELOCK).executeBatch(t, v, p, bytes32(0), SALT);
    }

    function _answer(address feed) internal view returns (uint256 price, uint256 age) {
        (, int256 a,, uint256 updatedAt,) = IFeed(feed).latestRoundData();
        return (uint256(a) * 1e10, block.timestamp - updatedAt);
    }

    function _readBacks(uint64 maxStaleness) internal {
        IGuard.FeedConfig memory c = IGuard(GUARD).feedConfig(PAIR);
        assertEq(c.stockFeed, F7, "guard reads the 24/7 feed");
        assertEq(c.maxStaleness, maxStaleness);
        assertEq(c.maxPriceDeviationBps, 300, "deviation bounds unchanged");
        assertEq(c.maxRemovalDeviationBps, 600);
        assertTrue(c.enabled && c.usdgFixedOne);
        assertEq(ILendingOracle(LENDING).assetToAggregator(NVDA), F7, "lending oracle reads the 24/7 feed");
        (uint256 p7, uint256 age7) = _answer(F7);
        uint256 margin = IPriceView(MARGIN_ORACLE).getPrice(NVDA);
        uint256 lending = IPriceView(LENDING_ADAPTER).getUnderlyingPrice(PNVDA);
        emit log_named_uint("24/7 answer age at the read-back (s)", age7);
        emit log_named_uint("margin oracle NVDA price (USD 1e18)", margin);
        emit log_named_uint("pNVDA lending price (controller scale)", lending);
        // The margin path is the guard's bound; the lending path keeps its own 72 h stock threshold.
        assertEq(margin, age7 <= maxStaleness ? p7 : 0, "margin follows the guard's bound");
        assertEq(IPriceView(GUARDED_SOURCE).assetPrices(NVDA), margin);
        assertEq(ILendingOracle(LENDING).assetPrices(NVDA), age7 <= 259_200 ? p7 : 0, "lending: 72 h stock threshold");
        assertGt(lending, 0);
    }

    function testSwitchKeepingTheTwelveHourBound() public {
        (uint256 p5, uint256 age5) = _answer(F5);
        emit log_named_uint("before: 24/5 answer age (s)", age5);
        emit log_named_uint("before: margin oracle NVDA price", IPriceView(MARGIN_ORACLE).getPrice(NVDA));
        p5;
        _switch(43_200);
        _readBacks(43_200);
    }

    function testSwitchWithTheHeartbeatBound() public {
        _switch(90_000); // the feed's 86,400 s heartbeat plus one hour
        _readBacks(90_000);
        assertGt(IPriceView(MARGIN_ORACLE).getPrice(NVDA), 0, "a heartbeat-bound guard prices through a quiet weekend");
    }

    function testOnlyTheLedgerSchedulesEarlyExecuteRefusedRunsOnceCancellable() public {
        vm.prank(LEDGER);
        ILendingOracle(LENDING).setAdmin(TIMELOCK);
        (address[] memory t, uint256[] memory v, bytes[] memory p) = _batch(43_200);
        vm.prank(address(0xbad));
        vm.expectRevert();
        ITimelock(TIMELOCK).scheduleBatch(t, v, p, bytes32(0), SALT, 3600);
        vm.prank(LEDGER);
        ITimelock(TIMELOCK).scheduleBatch(t, v, p, bytes32(0), SALT, 3600);
        vm.prank(LEDGER);
        vm.expectRevert();
        ITimelock(TIMELOCK).executeBatch(t, v, p, bytes32(0), SALT);
        bytes32 id = ITimelock(TIMELOCK).hashOperationBatch(t, v, p, bytes32(0), SALT);
        vm.prank(LEDGER);
        ITimelock(TIMELOCK).cancel(id);
        vm.warp(block.timestamp + 3600);
        vm.prank(LEDGER);
        vm.expectRevert();
        ITimelock(TIMELOCK).executeBatch(t, v, p, bytes32(0), SALT);
        assertEq(IGuard(GUARD).feedConfig(PAIR).stockFeed, F5, "cancelled: nothing changed");
    }

    function testWithoutThePrerequisiteTheBatchRevertsAtomically() public {
        (address[] memory t, uint256[] memory v, bytes[] memory p) = _batch(43_200);
        vm.prank(LEDGER);
        ITimelock(TIMELOCK).scheduleBatch(t, v, p, bytes32(0), SALT, 3600);
        vm.warp(block.timestamp + 3600);
        vm.prank(LEDGER);
        vm.expectRevert(); // the timelock is not a lending-oracle admin: the whole batch reverts, the guard is untouched
        ITimelock(TIMELOCK).executeBatch(t, v, p, bytes32(0), SALT);
        assertEq(IGuard(GUARD).feedConfig(PAIR).stockFeed, F5);
    }
}
