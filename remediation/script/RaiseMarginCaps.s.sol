// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Script } from "forge-std/Script.sol";
import { console2 } from "forge-std/console2.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {
    IsolatedMarginConfigUpgradeable
} from "peridot/margin/IsolatedMarginConfigUpgradeable.sol";
import { IsolatedMarginTypes } from "peridot/margin/IsolatedMarginTypes.sol";

interface IPriceSource {
    function getPrice(address asset) external view returns (uint256);
}

interface IMarketCash {
    function getCash() external view returns (uint256);
}

/// @notice Raises only the two dollar caps (max position value, max debt value) on the two live margin
/// pairs, through the config's own delay. Every other risk field must already equal the live 5x tuple.
/// @dev Fund the flash vault FIRST. Opening, closing and liquidating all borrow from it, so a debt cap
/// above what it holds would allow positions that cannot be closed or liquidated. This script refuses
/// to queue unless the flash vault and both markets can cover the new debt cap with headroom.
abstract contract RaiseMarginCapsBase is Script {
    address internal constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address internal constant CONFIG = 0x09F94fe0B79E000c8a26617c63E3427fdECB528b;
    address internal constant FLASH = 0x79d33c9BbC1D0711e88C5602f86135Ab4C088b06;
    address internal constant ORACLE = 0x63150Eb3DDf71420dA0b09b66838aab398f7dEdD;
    address internal constant USD = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address internal constant STOCK = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address internal constant P_USD = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    address internal constant P_STOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;

    /// New caps in USD 1e18. 5x means debt = 4x margin and gross = 1.25x debt, so these allow a
    /// margin of about $1.00 per position.
    uint128 internal constant NEW_MAX_POSITION_USD = 5e18;
    uint128 internal constant NEW_MAX_DEBT_USD = 4e18;
    /// Flash vault and market cash must exceed the debt cap by this factor (1.25x).
    uint256 internal constant HEADROOM_BPS = 12_500;

    function _live() internal pure returns (IsolatedMarginTypes.PairRiskConfig memory) {
        return IsolatedMarginTypes.PairRiskConfig(
            true, 500, 2000, 1000, 12500, 5000, 5000, 500, 100, 100, 2e18, 1e18
        );
    }

    function _target() internal pure returns (IsolatedMarginTypes.PairRiskConfig memory r) {
        r = _live();
        r.maxPositionValueUsd = NEW_MAX_POSITION_USD;
        r.maxDebtValueUsd = NEW_MAX_DEBT_USD;
    }

    function _config() internal pure returns (IsolatedMarginConfigUpgradeable) {
        return IsolatedMarginConfigUpgradeable(CONFIG);
    }

    function _pair(bool short) internal pure returns (address position, address debt) {
        return short ? (P_USD, P_STOCK) : (P_STOCK, P_USD);
    }

    function _action(bool short) internal view returns (bytes32) {
        (address position, address debt) = _pair(short);
        return
            keccak256(abi.encode("pairRisk", _config().pairKey(P_USD, position, debt), _target()));
    }

    function _readyAt(bool short) internal view returns (uint256) {
        return _config().queuedActions(_action(short));
    }

    /// 0 = still the live tuple, 1 = already the target, reverts on anything unexpected.
    function _state(bool short) internal view returns (uint256) {
        (address position, address debt) = _pair(short);
        bytes32 actual = keccak256(abi.encode(_config().getPairRisk(P_USD, position, debt)));
        if (actual == keccak256(abi.encode(_target()))) return 1;
        require(actual == keccak256(abi.encode(_live())), "UNEXPECTED_PAIR_RISK");
        return 0;
    }

    function _identity() internal view {
        require(block.chainid == 4663, "MAINNET_CHAIN_ONLY");
        require(_config().owner() == GOVERNOR, "GOVERNOR_CHANGED");
        require(_config().flashLoanProvider() == FLASH, "FLASH_CHANGED");
        require(!_config().opensPaused(), "OPENS_PAUSED");
        _state(false);
        _state(true);
    }

    /// Flash vault and both markets must be able to fund the new debt cap with headroom.
    function _requireLiquidity() internal view {
        uint256 stockPrice = IPriceSource(ORACLE).getPrice(STOCK); // reverts / zero if stale
        require(stockPrice != 0, "NO_STOCK_PRICE");
        uint256 need = uint256(NEW_MAX_DEBT_USD) * HEADROOM_BPS / 10_000; // USD 1e18
        // USDG-denominated debt (longs): flash vault and pUSDG cash, 6 decimals.
        uint256 needUsdg = need / 1e12;
        require(IERC20(USD).balanceOf(FLASH) >= needUsdg, "FLASH_USDG_TOO_LOW");
        require(IMarketCash(P_USD).getCash() >= needUsdg, "PUSDG_CASH_TOO_LOW");
        // NVDA-denominated debt (shorts): 18 decimals, priced in USD 1e18.
        uint256 needStock = need * 1e18 / stockPrice;
        require(IERC20(STOCK).balanceOf(FLASH) >= needStock, "FLASH_NVDA_TOO_LOW");
        require(IMarketCash(P_STOCK).getCash() >= needStock, "PNVDA_CASH_TOO_LOW");
    }
}

contract QueueRaiseMarginCaps is RaiseMarginCapsBase {
    function run() external {
        _identity();
        _requireLiquidity();
        vm.startBroadcast(GOVERNOR);
        for (uint256 i; i < 2; ++i) {
            bool short = i == 1;
            if (_state(short) == 1 || _readyAt(short) != 0) continue;
            (address position, address debt) = _pair(short);
            _config().queuePairRisk(P_USD, position, debt, _target());
        }
        vm.stopBroadcast();
        console2.log("Long ready at:", _readyAt(false));
        console2.log("Short ready at:", _readyAt(true));
        console2.log("Caps queued: position $5, debt $4 per position. Nothing else changes.");
    }
}

contract ApplyRaiseMarginCaps is RaiseMarginCapsBase {
    function run() external {
        _identity();
        _requireLiquidity(); // re-checked at apply time: the vault must still be funded
        for (uint256 i; i < 2; ++i) {
            if (_state(i == 1) == 0) {
                require(_readyAt(i == 1) != 0 && block.timestamp >= _readyAt(i == 1), "CAPS_DELAY");
            }
        }
        vm.startBroadcast(GOVERNOR);
        for (uint256 i; i < 2; ++i) {
            bool short = i == 1;
            if (_state(short) == 1) continue;
            (address position, address debt) = _pair(short);
            _config().setPairRisk(P_USD, position, debt, _target());
        }
        vm.stopBroadcast();
        require(_state(false) == 1 && _state(true) == 1, "NOT_APPLIED");
        console2.log("Caps applied: position $5, debt $4 per position.");
    }
}
