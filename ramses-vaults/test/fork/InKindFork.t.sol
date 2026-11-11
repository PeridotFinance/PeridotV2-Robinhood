// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { LendingForkBase } from "./LendingFork.t.sol";
import { RamsesBoostedVault } from "../../src/RamsesBoostedVault.sol";
import { RamsesOracleGuard } from "../../src/RamsesOracleGuard.sol";
import { RamsesLossReserve } from "../../src/RamsesLossReserve.sol";
import { RamsesBoostedDelegate } from "../../src/lending/RamsesBoostedDelegate.sol";
import { IRamsesPool, IRamsesGauge } from "../../src/interfaces/IRamsesV3.sol";
import { IAggregatorV3 } from "baseline/src/interfaces/IAggregatorV3.sol";
import { InKindScenarios } from "../inkind/InKindScenarios.sol";

/// @notice The in-kind scenarios on a mainnet fork: real pool, position manager, gauge and tokens with real transfers and real swaps; the repository's vault, markets and
/// reserve deployed on the fork behind the test-only router; later feed updates SIMULATED with vm.mockCall. Mechanism proofs at small size, not capacity.
abstract contract InKindForkBase is LendingForkBase, InKindScenarios {
    function _newVault() internal override returns (RamsesBoostedVault) {
        return _routed();
    }

    function _v() internal view override returns (RamsesBoostedVault) { return vault; }
    function _g() internal view override returns (RamsesOracleGuard) { return guard; }
    function _r() internal view override returns (RamsesLossReserve) { return reserve; }
    function _sm() internal view override returns (RamsesBoostedDelegate) { return sMarket; }
    function _um() internal view override returns (RamsesBoostedDelegate) { return uMarket; }
    function _st() internal view override returns (IERC20) { return stock; }
    function _us() internal view override returns (IERC20) { return usdg; }
    function _pair() internal view override returns (bytes32) { return PAIR; }
    function _keeper() internal view override returns (address) { return keeper; }
    function _alice() internal view override returns (address) { return alice; }
    function _bob() internal view override returns (address) { return bob; }
    function _ready() internal override { }
    function _enableBorrowing() internal override { _enableBorrow(); }
    function _stockFalls(int24 t) internal override { _shift(crashSign * t); }
    function _refreshFeed() internal override { _shift(0); }

    function _poolOnly(int24 t) internal override {
        (, int24 tick,,,,,) = pool.slot0();
        trader.move(t < 0, TickMath.getSqrtPriceAtTick(tick + t));
    }
}

contract NvdaInKindForkTest is InKindForkBase { }

contract SpyInKindForkTest is InKindForkBase {
    function _configure() internal override {
        PAIR = keccak256("SPY/USDG");
        pool = IRamsesPool(0x38453c115607463Ac284820Ce959831042f3Df4E);
        gauge = IRamsesGauge(0x739E31798EFE8Cff3ef4c613D8ae751e179C7337);
        feed = IAggregatorV3(0x319724394D3A0e3669269846abE664Cd621f9f6A);
        stockIsToken0 = true;
        crashSign = -1;
    }
}
