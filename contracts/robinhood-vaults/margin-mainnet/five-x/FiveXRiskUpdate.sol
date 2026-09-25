// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {
    IsolatedMarginConfigUpgradeable
} from "peridot/margin/IsolatedMarginConfigUpgradeable.sol";
import { IsolatedMarginTypes } from "peridot/margin/IsolatedMarginTypes.sol";

/// @notice Update only the two deployed NVDA/USDG risk tuples; preserve dollar caps and all other risk fields.
abstract contract FiveXRiskUpdate {
    address internal constant FIVE_X_GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address internal constant FIVE_X_CONFIG = 0x09F94fe0B79E000c8a26617c63E3427fdECB528b;
    address internal constant FIVE_X_USD = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    address internal constant FIVE_X_STOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;

    function fiveXRisk() public pure returns (IsolatedMarginTypes.PairRiskConfig memory) {
        return IsolatedMarginTypes.PairRiskConfig(
            true, 500, 2000, 1000, 12500, 5000, 5000, 500, 100, 100, 2e18, 1e18
        );
    }

    function _oldRisk() internal pure returns (IsolatedMarginTypes.PairRiskConfig memory) {
        return IsolatedMarginTypes.PairRiskConfig(
            true, 200, 5000, 2500, 12500, 5000, 5000, 500, 100, 100, 2e18, 1e18
        );
    }

    function _fiveXConfig() internal pure returns (IsolatedMarginConfigUpgradeable) {
        return IsolatedMarginConfigUpgradeable(FIVE_X_CONFIG);
    }

    function _pair(bool short) internal pure returns (address position, address debt) {
        return short ? (FIVE_X_USD, FIVE_X_STOCK) : (FIVE_X_STOCK, FIVE_X_USD);
    }

    function fiveXAction(bool short) public view returns (bytes32) {
        (address position, address debt) = _pair(short);
        return keccak256(
            abi.encode("pairRisk", _fiveXConfig().pairKey(FIVE_X_USD, position, debt), fiveXRisk())
        );
    }

    function fiveXReadyAt(bool short) public view returns (uint256) {
        return _fiveXConfig().queuedActions(fiveXAction(short));
    }

    function _applied(bool short) internal view returns (bool) {
        (address position, address debt) = _pair(short);
        bytes32 actual =
            keccak256(abi.encode(_fiveXConfig().getPairRisk(FIVE_X_USD, position, debt)));
        if (actual == keccak256(abi.encode(fiveXRisk()))) return true;
        require(actual == keccak256(abi.encode(_oldRisk())), "UNEXPECTED_PAIR_RISK");
        return false;
    }

    function _checkFiveXIdentity() internal view {
        require(block.chainid == 4663, "MAINNET_CHAIN_ONLY");
        require(_fiveXConfig().owner() == FIVE_X_GOVERNOR, "GOVERNOR_CHANGED");
        require(
            _fiveXConfig().routerAdapter() == 0xa32C34F100B4F1f36ECA09c427a098f99F4423F0,
            "ROUTER_CHANGED"
        );
        require(
            _fiveXConfig().flashLoanProvider() == 0x79d33c9BbC1D0711e88C5602f86135Ab4C088b06,
            "FLASH_CHANGED"
        );
        _applied(false);
        _applied(true);
    }

    function _queueFiveX() internal {
        _checkFiveXIdentity();
        for (uint256 i; i < 2; ++i) {
            bool short = i == 1;
            if (_applied(short) || fiveXReadyAt(short) != 0) continue;
            (address position, address debt) = _pair(short);
            _fiveXConfig().queuePairRisk(FIVE_X_USD, position, debt, fiveXRisk());
        }
    }

    function _applyFiveX() internal {
        _checkFiveXIdentity();
        // Check both directions before emitting any transaction. A partial mined run can safely continue.
        for (uint256 i; i < 2; ++i) {
            if (!_applied(i == 1)) {
                require(
                    fiveXReadyAt(i == 1) != 0 && block.timestamp >= fiveXReadyAt(i == 1),
                    "FIVE_X_DELAY"
                );
            }
        }
        for (uint256 i; i < 2; ++i) {
            bool short = i == 1;
            if (_applied(short)) continue;
            (address position, address debt) = _pair(short);
            _fiveXConfig().setPairRisk(FIVE_X_USD, position, debt, fiveXRisk());
        }
    }
}
