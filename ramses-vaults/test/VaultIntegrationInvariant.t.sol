// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { Test } from "forge-std/Test.sol";
import { StdInvariant } from "forge-std/StdInvariant.sol";
import { Phase2Fixture } from "./Phase2.t.sol";
import { RamsesBoostedVault } from "../src/RamsesBoostedVault.sol";
import { TokenDouble } from "./mocks/RamsesV3Double.sol";
import { PairLedger } from "baseline/src/libraries/VaultTypes.sol";

contract RamsesVaultHandler is Test {
    RamsesBoostedVault immutable vault;
    TokenDouble immutable stock;
    TokenDouble immutable usdg;
    bytes32 constant PAIR = keccak256("NVDA/USDG");
    address constant STOCK_ACCOUNT = address(0x789);
    address constant USDG_ACCOUNT = address(0xabc);
    address constant KEEPER = address(0x123);
    uint256 public successes;

    constructor(RamsesBoostedVault v, TokenDouble s, TokenDouble u) {
        vault = v;
        stock = s;
        usdg = u;
    }

    function deposit(bool stockSide, uint64 seed) external {
        address account = stockSide ? STOCK_ACCOUNT : USDG_ACCOUNT;
        TokenDouble token = stockSide ? stock : usdg;
        uint256 amount = stockSide ? bound(uint256(seed), 1, 1e18) : bound(uint256(seed), 1, 100e6);
        token.mint(account, amount);
        vm.prank(account);
        try vault.depositForPair(PAIR, address(token), amount) {
            ++successes;
        } catch { }
    }

    function withdraw(bool stockSide, uint64 seed) external {
        address account = stockSide ? STOCK_ACCOUNT : USDG_ACCOUNT;
        address token = stockSide ? address(stock) : address(usdg);
        uint256 claim = vault.accountedAssets(PAIR, token);
        if (claim == 0) return;
        uint256 amount = bound(uint256(seed), 1, claim);
        vm.prank(account);
        try vault.withdrawForSide(PAIR, token, amount, account, block.timestamp) {
            ++successes;
        } catch { }
    }

    function checkpoint() external {
        vm.prank(KEEPER);
        vault.checkpoint(PAIR, block.timestamp);
        ++successes;
    }

    function rebalance() external {
        vm.prank(KEEPER);
        try vault.rebalance(PAIR, block.timestamp) {
            ++successes;
        } catch { }
    }

    function harvest() external {
        vm.prank(KEEPER);
        vault.harvest(PAIR, block.timestamp);
        ++successes;
    }
}

contract VaultIntegrationInvariant is StdInvariant, Phase2Fixture {
    RamsesVaultHandler handler;

    function setUp() public override {
        super.setUp();
        deposit(2e18, 200e6);
        recenter();
        fundRewards();
        vm.warp(vm.getBlockTimestamp() + 1 hours);
        handler = new RamsesVaultHandler(vault, TokenDouble(address(stock)), usdg);
        targetContract(address(handler));
    }

    function invariantIdleAndRamNeverExceedCustody() external view {
        PairLedger memory l = vault.ledger(PAIR);
        assertLe(l.stockIdle, stock.balanceOf(address(vault)));
        assertLe(l.usdgIdle, usdg.balanceOf(address(vault)));
        assertLe(vault.externalRewards(PAIR, address(ram)), ram.balanceOf(address(vault)));
    }

    function invariantAggregateEqualsPairClaim() external view {
        assertEq(vault.aggregateUsdgPrincipal(address(usdg)), vault.ledger(PAIR).usdgPrincipal);
    }

    function invariantNoStandingAdapterOrManagerApprovals() external view {
        noApprovals();
    }

    function invariantPositionCustodyAndForfeiture() external view {
        uint256 id = adapter.positionState(PAIR).tokenId;
        if (id != 0) assertEq(manager.ownerOf(id), address(adapter));
        assertFalse(adapter.allowRewardForfeiture(PAIR));
    }

    function invariantOracleValueCoversClaimsWithinRounding() external view {
        (uint256 s, uint256 u) = vault.totalPairAssets(PAIR);
        PairLedger memory l = vault.ledger(PAIR);
        // Spot differs from the feed by < one tick; include 1bp plus operation rounding.
        uint256 gross = s * 100 + u * 1e12;
        uint256 claims = l.stockPrincipal * 100 + l.usdgPrincipal * 1e12;
        assertGe(gross + claims / 10000 + 1e14, claims);
    }
}
