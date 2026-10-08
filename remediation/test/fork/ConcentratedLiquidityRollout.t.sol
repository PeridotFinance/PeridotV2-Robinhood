// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Test } from "forge-std/Test.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { TimelockController } from "@openzeppelin/contracts/governance/TimelockController.sol";
import { PErc20 } from "peridot/PErc20.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { RobinhoodBoostedVaultV3 } from "../../src/RobinhoodBoostedVaultV3.sol";
import { UniswapV4PairedAdapterV3 } from "../../src/UniswapV4PairedAdapterV3.sol";
import {
    DeployAndQueueConcentratedLiquidity,
    ExecuteConcentratedLiquidityUpgrade
} from "../../script/UpgradeConcentratedLiquidity.s.sol";
import { IGuardPrices } from "./LendingDelegateMarginCompat.t.sol";

/// @notice The rollout scripts run exactly as the governor will run them, on a local fork of live
/// state, then the keeper path and ordinary supplier flows on the upgraded system.
contract ConcentratedLiquidityRolloutForkTest is Test {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant TIMELOCK = 0x6797FB8Ce049B42C5BC2b42Bf76c6d15C7B12498;
    address constant VAULT = 0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f;
    address constant ADAPTER = 0xadA73211711e4790bc83B5d6B39f47fE04D276f3;
    address constant GUARD = 0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741;
    address constant P_USD = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    address constant P_STOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    bytes32 constant PAIR = keccak256("NVDA/USDG");

    RobinhoodBoostedVaultV3 internal vault = RobinhoodBoostedVaultV3(VAULT);
    UniswapV4PairedAdapterV3 internal adapter = UniswapV4PairedAdapterV3(ADAPTER);

    function setUp() public {
        vm.createSelectFork(vm.envString("ROBINHOOD_RPC_URL"), vm.envUint("REMEDIATION_FORK_BLOCK"));
        vm.roll(vm.envUint("REMEDIATION_NATIVE_BLOCK"));
        assertEq(block.chainid, 4663);
        try IGuardPrices(GUARD).pricesUSD18(PAIR) returns (uint256 s, uint256 u) {
            if (s == 0 || u == 0) vm.skip(true);
        } catch {
            vm.skip(true);
        }
    }

    function _queue() internal returns (address adapterImpl, address vaultImpl) {
        // Tests share one process environment: start from "nothing deployed yet".
        vm.setEnv("NEW_ADAPTER_IMPLEMENTATION", "0x0000000000000000000000000000000000000000");
        vm.setEnv("NEW_VAULT_IMPLEMENTATION", "0x0000000000000000000000000000000000000000");
        uint256 nonce = vm.getNonce(GOVERNOR);
        adapterImpl = vm.computeCreateAddress(GOVERNOR, nonce);
        vaultImpl = vm.computeCreateAddress(GOVERNOR, nonce + 1);
        new DeployAndQueueConcentratedLiquidity().run();
        assertGt(adapterImpl.code.length, 0);
        assertGt(vaultImpl.code.length, 0);
        vm.setEnv("NEW_ADAPTER_IMPLEMENTATION", vm.toString(adapterImpl));
        vm.setEnv("NEW_VAULT_IMPLEMENTATION", vm.toString(vaultImpl));
    }

    function _delay() internal view returns (uint256) {
        return TimelockController(payable(TIMELOCK)).getMinDelay();
    }

    function testRolloutQueueExecuteThenKeeperAndSuppliers() public {
        bytes32 ledgerBefore = keccak256(abi.encode(vault.ledger(PAIR)));
        PairConfig memory configBefore = vault.pairConfig(PAIR);
        assertFalse(configBefore.allocationPaused, "pre-state: LP allocation is open");
        uint128 fullRangeLiquidity = adapter.positionState(PAIR).liquidity;
        assertGt(fullRangeLiquidity, 0, "pre-state: a full-range position is open");
        uint256 usdRate = PErc20(P_USD).exchangeRateStored();
        uint256 stockRate = PErc20(P_STOCK).exchangeRateStored();
        (address adapterImpl, address vaultImpl) = _queue();
        uint256 queuedAt = vm.getBlockTimestamp();

        // Nothing has moved yet.
        assertEq(keccak256(abi.encode(vault.ledger(PAIR))), ledgerBefore);
        ExecuteConcentratedLiquidityUpgrade upgrade = new ExecuteConcentratedLiquidityUpgrade();

        vm.warp(queuedAt + _delay() - 1);
        vm.expectRevert("TIMELOCK_NOT_READY");
        upgrade.run();

        vm.warp(queuedAt + _delay());
        upgrade.run();

        // Upgrade verified: flags untouched, the position is still the legacy full range.
        assertEq(abi.encode(vault.pairConfig(PAIR)), abi.encode(configBefore));
        assertEq(PErc20(P_USD).exchangeRateStored(), usdRate);
        assertEq(PErc20(P_STOCK).exchangeRateStored(), stockRate);
        (,, bool rangedBefore) = adapter.positionTicks(PAIR);
        assertFalse(rangedBefore);
        assertEq(adapter.positionState(PAIR).liquidity, fullRangeLiquidity);
        vm.expectRevert("TIMELOCK_NOT_READY"); // an executed operation cannot run twice
        upgrade.run();

        // First keeper action converts the live full-range position.
        vm.prank(GOVERNOR);
        vault.recenter(PAIR, vm.getBlockTimestamp() + 120);
        (int24 lower, int24 upper, bool ranged) = adapter.positionTicks(PAIR);
        assertTrue(ranged);
        assertEq(int256(upper) - int256(lower), 2 * 1200 + 60);
        assertGt(adapter.positionState(PAIR).liquidity, 0);
        emit log_named_int("lower", lower);
        emit log_named_int("upper", upper);
        emit log_named_uint("liquidity", adapter.positionState(PAIR).liquidity);

        {
            PairLedger memory l = vault.ledger(PAIR);
            emit log_named_uint("stockPrincipal", l.stockPrincipal);
            emit log_named_uint("usdgPrincipal", l.usdgPrincipal);
            emit log_named_uint("cumulativeLossUSDG", l.cumulativeLossUSDG);
        }
        // Suppliers cannot lose NAV to the conversion (rounding only); the exit may collect the
        // position's accrued fees, which is a small gain, so allow a little upside.
        _assertRateNotWorse(PErc20(P_USD).exchangeRateStored(), usdRate);
        _assertRateNotWorse(PErc20(P_STOCK).exchangeRateStored(), stockRate);

        // Ordinary suppliers still get paid through the open ranged LP.
        uint256 shares = IERC20(P_USD).balanceOf(GOVERNOR);
        uint256 before_ = IERC20(0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168).balanceOf(GOVERNOR);
        uint256 take = shares / 10 * 9;
        uint256 expected = take * PErc20(P_USD).exchangeRateCurrent() / 1e18;
        vm.prank(GOVERNOR);
        assertEq(PErc20(P_USD).redeem(take), 0);
        uint256 paid =
            IERC20(0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168).balanceOf(GOVERNOR) - before_;
        assertGe(paid, expected * 999 / 1000);
        assertEq(
            address(
                uint160(
                    uint256(
                        vm.load(
                            VAULT,
                            bytes32(
                                uint256(
                                    0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc
                                )
                            )
                        )
                    )
                )
            ),
            vaultImpl
        );
        assertEq(
            address(
                uint160(
                    uint256(
                        vm.load(
                            ADAPTER,
                            bytes32(
                                uint256(
                                    0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc
                                )
                            )
                        )
                    )
                )
            ),
            adapterImpl
        );
    }

    function _assertRateNotWorse(uint256 after_, uint256 before_) internal pure {
        assertGe(after_ * (1e18 + 1e9) / 1e18, before_, "NAV must not fall");
        assertLe(after_, before_ * (1e18 + 1e15) / 1e18, "implausible gain from a conversion");
    }

    function testQueueTwiceIsRefused() public {
        _queue(); // sets NEW_*_IMPLEMENTATION, so the second run reuses them and finds the operation
        DeployAndQueueConcentratedLiquidity again = new DeployAndQueueConcentratedLiquidity();
        vm.expectRevert("UPGRADE_ALREADY_QUEUED");
        again.run();
    }
}
