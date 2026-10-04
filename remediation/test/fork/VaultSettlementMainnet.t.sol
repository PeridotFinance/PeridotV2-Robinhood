// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";
import { RobinhoodBoostedVaultV2 } from "../../src/RobinhoodBoostedVaultV2.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { IUniswapV4PairedAdapter } from "baseline/src/interfaces/IUniswapV4PairedAdapter.sol";
import { RecoverCanaryResidue } from "../../script/RecoverCanaryResidue.s.sol";

interface ISettlementMarket {
    function syncVault(uint256 amount) external returns (uint256 returned, uint256 realizedLoss);
    function totalBorrows() external view returns (uint256);
}

/// @notice Read-only fork rehearsal against the installed implementation. No key, broadcast,
///         injected balances or oracle overrides. Advancing time is local to the timelock test.
contract VaultSettlementMainnetForkTest is Test {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    address constant STOCK = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address constant USDG = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address constant PSTOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    address constant PUSDG = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    bytes32 constant PRODUCTION = keccak256("NVDA/USDG");
    bytes32 constant CANARY = 0x536e330d7e6d12c73d1ae0547dfec4ea4d47ad94f4244a096ea5fad4f87f28ee;
    bytes32 constant SLOT = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
    RobinhoodBoostedVaultV2 internal vault = RobinhoodBoostedVaultV2(VAULT);

    function setUp() external {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        address implementation = address(uint160(uint256(vm.load(VAULT, SLOT))));
        assertEq(
            implementation.codehash,
            0xfd8fba1858dc625afd24cdbf0d0461329ae83943cb7639800e4618c762c48c84
        );
        assertTrue(vault.hasRole(vault.KEEPER_ROLE(), GOVERNOR));
        assertTrue(vault.hasRole(vault.GUARDIAN_ROLE(), GOVERNOR));
        assertTrue(vault.hasRole(vault.CONFIG_ROLE(), TIMELOCK));
        for (uint256 i; i < 2; ++i) {
            bytes32 pair = i == 0 ? PRODUCTION : CANARY;
            PairConfig memory c = vault.pairConfig(pair);
            assertTrue(c.exists && c.allocationPaused && c.swapsPaused && !c.emergencyMode);
            assertEq(c.stockToken, STOCK);
            assertEq(c.usdg, USDG);
            assertEq(vault.liquidityAdapter().positionState(pair).liquidity, 0);
        }
        assertEq(vault.pairConfig(PRODUCTION).stockAccount, PSTOCK);
        assertEq(vault.pairConfig(PRODUCTION).usdgAccount, PUSDG);
        assertEq(vault.pairConfig(CANARY).stockAccount, GOVERNOR);
        assertEq(vault.pairConfig(CANARY).usdgAccount, GOVERNOR);
        assertEq(ISettlementMarket(PSTOCK).totalBorrows(), 0);
        assertEq(ISettlementMarket(PUSDG).totalBorrows(), 0);
    }

    function testCanaryRecoveryReturnsExactResidueAndPreservesProduction() external {
        bytes32 productionBefore = keccak256(abi.encode(vault.ledger(PRODUCTION)));
        bytes32 configBefore = keccak256(abi.encode(vault.pairConfig(PRODUCTION)));
        PairLedger memory beforeLedger = vault.ledger(CANARY);
        assertEq(beforeLedger.stockPrincipal, 0);
        assertEq(beforeLedger.usdgPrincipal, 0);
        assertEq(beforeLedger.stockIdle, 24_697_449_583);
        assertEq(beforeLedger.usdgIdle, 0);
        assertEq(vault.liquidityAdapter().positionState(CANARY).tokenId, 0);
        uint256 balance = IERC20(STOCK).balanceOf(GOVERNOR);
        vm.startPrank(GOVERNOR);
        vault.checkpoint(CANARY, block.timestamp);
        assertEq(vault.ledger(CANARY).stockPrincipal, beforeLedger.stockIdle);
        (uint256 returned, uint256 loss) =
            vault.withdrawForSide(CANARY, STOCK, beforeLedger.stockIdle, GOVERNOR, block.timestamp);
        vm.stopPrank();
        assertEq(returned, beforeLedger.stockIdle);
        assertEq(loss, 0);
        assertEq(IERC20(STOCK).balanceOf(GOVERNOR) - balance, returned);
        _assertDrained(CANARY);
        assertEq(keccak256(abi.encode(vault.ledger(PRODUCTION))), productionBefore);
        assertEq(keccak256(abi.encode(vault.pairConfig(PRODUCTION))), configBefore);
    }

    function testCanaryRecoveryRejectsWrongSideCaller() external {
        vm.prank(GOVERNOR);
        vault.checkpoint(CANARY, block.timestamp);
        vm.prank(PSTOCK);
        vm.expectRevert(RobinhoodBoostedVaultV2.UnauthorizedSide.selector);
        vault.withdrawForSide(CANARY, STOCK, 1, PSTOCK, block.timestamp);
    }

    function testExactCanarySigningScriptAndRepeatRefusal() external {
        RecoverCanaryResidue recovery = new RecoverCanaryResidue();
        recovery.run();
        _assertDrained(CANARY);
        vm.expectRevert("CANARY_STATE_CHANGED_OR_PARTIAL_ATTEMPT");
        recovery.run();
    }

    function testProductionSettlementStockFirstThroughActualTimelock() external {
        _settlementWindDown(true);
    }

    function testProductionSettlementDollarFirstThroughActualTimelock() external {
        _settlementWindDown(false);
    }

    function _settlementWindDown(bool stockFirst) internal {
        bytes32 canaryBefore = keccak256(abi.encode(vault.ledger(CANARY)));
        uint256 stockCash = IERC20(STOCK).balanceOf(PSTOCK);
        uint256 usdgCash = IERC20(USDG).balanceOf(PUSDG);
        TimelockController timelock = TimelockController(payable(TIMELOCK));
        uint256 delay = timelock.getMinDelay();
        assertGe(delay, 1 hours);
        bytes memory payload = abi.encodeCall(vault.setPairPause, (PRODUCTION, true, false, false));
        bytes32 salt = keccak256("PERIDOT_SETTLEMENT_REHEARSAL_2026_10_01");
        bytes32 operation = timelock.hashOperation(VAULT, 0, payload, bytes32(0), salt);
        vm.prank(GOVERNOR);
        timelock.schedule(VAULT, 0, payload, bytes32(0), salt, delay);
        vm.warp(block.timestamp + delay - 1);
        assertFalse(timelock.isOperationReady(operation));
        vm.warp(block.timestamp + 1);
        vm.roll(block.number + delay / 12);
        vm.prank(GOVERNOR);
        timelock.execute(VAULT, 0, payload, bytes32(0), salt);
        assertTrue(timelock.isOperationDone(operation));
        assertTrue(vault.pairConfig(PRODUCTION).allocationPaused);
        assertFalse(vault.pairConfig(PRODUCTION).swapsPaused);
        vm.startPrank(GOVERNOR);
        vault.checkpoint(PRODUCTION, block.timestamp);
        if (vault.liquidityAdapter().positionState(PRODUCTION).tokenId != 0) {
            vault.burnEmptyPosition(PRODUCTION, block.timestamp);
            vault.checkpoint(PRODUCTION, block.timestamp);
        }
        for (uint256 i; i < 3; ++i) {
            _sync(stockFirst ? PSTOCK : PUSDG, stockFirst ? STOCK : USDG);
            _sync(stockFirst ? PUSDG : PSTOCK, stockFirst ? USDG : STOCK);
            vault.checkpoint(PRODUCTION, block.timestamp);
        }
        vault.setPairPause(PRODUCTION, true, true, false);
        vm.stopPrank();
        _assertDrained(PRODUCTION);
        assertEq(keccak256(abi.encode(vault.ledger(CANARY))), canaryBefore);
        assertGe(IERC20(STOCK).balanceOf(PSTOCK), stockCash);
        assertGe(IERC20(USDG).balanceOf(PUSDG), usdgCash);
        emit log_named_uint("stock returned to pNVDA", IERC20(STOCK).balanceOf(PSTOCK) - stockCash);
        emit log_named_uint("USDG returned to pUSDG", IERC20(USDG).balanceOf(PUSDG) - usdgCash);
        assertTrue(vault.pairConfig(PRODUCTION).allocationPaused);
        assertTrue(vault.pairConfig(PRODUCTION).swapsPaused);
        assertEq(IERC20(STOCK).allowance(VAULT, address(vault.liquidityAdapter())), 0);
        assertEq(IERC20(USDG).allowance(VAULT, address(vault.liquidityAdapter())), 0);
    }

    function _sync(address market, address token) internal {
        uint256 claim = vault.accountedAssets(PRODUCTION, token);
        if (claim == 0) return;
        uint256 balance = IERC20(token).balanceOf(market);
        (uint256 returned, uint256 loss) =
            ISettlementMarket(market).syncVault{ gas: 2_000_000 }(claim);
        assertGt(returned, 0, "Caught withdrawal failure or no progress");
        assertEq(IERC20(token).balanceOf(market) - balance, returned);
        assertEq(vault.accountedAssets(PRODUCTION, token) + returned + loss, claim);
    }

    function _assertDrained(bytes32 pair) internal view {
        PairLedger memory l = vault.ledger(pair);
        assertEq(l.stockPrincipal, 0);
        assertEq(l.usdgPrincipal, 0);
        assertEq(l.stockIdle, 0);
        assertEq(l.usdgIdle, 0);
        IUniswapV4PairedAdapter.PositionState memory p =
            vault.liquidityAdapter().positionState(pair);
        assertEq(p.tokenId, 0);
        assertEq(p.liquidity, 0);
        assertEq(p.stockAmount, 0);
        assertEq(p.usdgAmount, 0);
    }
}
