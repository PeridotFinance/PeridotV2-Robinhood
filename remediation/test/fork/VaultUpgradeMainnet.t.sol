// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { ProxyAdmin } from "@openzeppelin/contracts/proxy/transparent/ProxyAdmin.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";
import {
    ITransparentUpgradeableProxy
} from "@openzeppelin/contracts/proxy/transparent/TransparentUpgradeableProxy.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { RobinhoodBoostedVaultV2 } from "../../src/RobinhoodBoostedVaultV2.sol";
import { PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { IStockOracleGuard } from "baseline/src/interfaces/IStockOracleGuard.sol";
import {
    DeployAndQueueNativeBacking,
    ExecuteNativeBacking
} from "../../script/UpgradeNativeBacking.s.sol";

interface IMarketViews {
    function exchangeRateStored() external view returns (uint256);
    function getCash() external view returns (uint256);
}

contract VaultUpgradeMainnetForkTest is Test {
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    address constant ADMIN = 0xad2165E6f3b8146D17815968470eDb8B9a0A4ab7;
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    address constant GUARD = 0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741;
    address constant USDG = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address constant STOCK = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address constant PUSDG = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    address constant PSTOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    bytes32 constant PAIR = keccak256("NVDA/USDG");
    bytes32 constant IMPLEMENTATION_SLOT =
        0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
    RobinhoodBoostedVaultV2 internal vault = RobinhoodBoostedVaultV2(VAULT);

    function setUp() external {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        assertEq(block.chainid, 4663);
        assertEq(ProxyAdmin(ADMIN).owner(), TIMELOCK);
    }

    function _upgrade() internal returns (address implementation) {
        implementation = address(new RobinhoodBoostedVaultV2());
        vm.prank(TIMELOCK);
        ProxyAdmin(ADMIN).upgradeAndCall(ITransparentUpgradeableProxy(VAULT), implementation, "");
        assertEq(address(uint160(uint256(vm.load(VAULT, IMPLEMENTATION_SLOT)))), implementation);
    }

    function testQueueAndExecuteScriptsRespectRealTimelock() external {
        address governor = 0x94696d767e65a75581145646960FA0eC886cE5d2;
        // Three pause transactions precede CREATE in the reviewed runner.
        address candidate = vm.computeCreateAddress(governor, vm.getNonce(governor) + 3);
        uint256 delay = TimelockController(payable(TIMELOCK)).getMinDelay();
        uint256 queuedAt = block.timestamp;
        vm.setEnv("NEW_VAULT_IMPLEMENTATION", "0x0000000000000000000000000000000000000000");
        new DeployAndQueueNativeBacking().run();
        assertGt(candidate.code.length, 0);
        bytes32 ledger = keccak256(abi.encode(vault.ledger(PAIR)));
        assertTrue(vault.pairConfig(PAIR).allocationPaused);
        vm.setEnv("NEW_VAULT_IMPLEMENTATION", vm.toString(candidate));
        ExecuteNativeBacking executor = new ExecuteNativeBacking();
        vm.warp(queuedAt + delay - 1);
        vm.expectRevert("TIMELOCK_NOT_READY");
        executor.run();
        vm.warp(queuedAt + delay);
        executor.run();
        assertEq(address(uint160(uint256(vm.load(VAULT, IMPLEMENTATION_SLOT)))), candidate);
        assertEq(keccak256(abi.encode(vault.ledger(PAIR))), ledger);
    }

    function testActualProxyUpgradePreservesPairStateAndAuthorities() external {
        bytes memory ledgerBefore = abi.encode(vault.ledger(PAIR));
        bytes memory configBefore = abi.encode(vault.pairConfig(PAIR));
        bytes memory canaryBefore = abi.encode(
            vault.ledger(0x536e330d7e6d12c73d1ae0547dfec4ea4d47ad94f4244a096ea5fad4f87f28ee)
        );
        uint256 aggregate = vault.aggregateUsdgPrincipal(USDG);
        address adapter = address(vault.liquidityAdapter());
        address reserve = address(vault.lossReserve());
        _upgrade();
        assertEq(abi.encode(vault.ledger(PAIR)), ledgerBefore);
        assertEq(abi.encode(vault.pairConfig(PAIR)), configBefore);
        assertEq(
            abi.encode(
                vault.ledger(0x536e330d7e6d12c73d1ae0547dfec4ea4d47ad94f4244a096ea5fad4f87f28ee)
            ),
            canaryBefore
        );
        assertEq(vault.aggregateUsdgPrincipal(USDG), aggregate);
        assertEq(address(vault.oracleGuard()), GUARD);
        assertEq(address(vault.liquidityAdapter()), adapter);
        assertEq(address(vault.lossReserve()), reserve);
        assertTrue(vault.hasRole(vault.DEFAULT_ADMIN_ROLE(), TIMELOCK));
        assertTrue(vault.hasRole(vault.CONFIG_ROLE(), TIMELOCK));
        assertEq(ProxyAdmin(ADMIN).owner(), TIMELOCK);
    }

    function testRealMarketsKeepNAVButExcludeGuardBlockedVaultCash() external {
        PairLedger memory ledger = vault.ledger(PAIR);
        assertLt(
            ledger.stockIdle,
            ledger.stockPrincipal,
            "Refresh fixture: expected native stock deficit"
        );
        assertEq(vault.liquidityAdapter().positionState(PAIR).liquidity, 0);
        uint256 dollarRate = IMarketViews(PUSDG).exchangeRateStored();
        uint256 stockRate = IMarketViews(PSTOCK).exchangeRateStored();
        vm.mockCallRevert(
            GUARD,
            abi.encodeWithSelector(IStockOracleGuard.validateRemovalPrice.selector),
            abi.encodeWithSignature("Error(string)", "GUARD_UNAVAILABLE")
        );
        assertGt(vault.withdrawableAssets(PAIR, USDG), 0, "old zero-LP bypass");
        _upgrade();
        assertEq(vault.withdrawableAssets(PAIR, USDG), 0);
        assertEq(vault.withdrawableAssets(PAIR, STOCK), 0);
        assertEq(IMarketViews(PUSDG).getCash(), IERC20(USDG).balanceOf(PUSDG));
        assertEq(IMarketViews(PSTOCK).getCash(), IERC20(STOCK).balanceOf(PSTOCK));
        assertEq(
            IMarketViews(PUSDG).exchangeRateStored(),
            dollarRate,
            "liquidity restriction must not reduce NAV"
        );
        assertEq(IMarketViews(PSTOCK).exchangeRateStored(), stockRate);
        vm.prank(PUSDG);
        vm.expectRevert("GUARD_UNAVAILABLE");
        vault.withdrawForSide(PAIR, USDG, 1, PUSDG, block.timestamp + 60);
    }
}
