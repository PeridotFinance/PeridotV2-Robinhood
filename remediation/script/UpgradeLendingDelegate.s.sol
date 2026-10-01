// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Script } from "forge-std/Script.sol";
import { console2 } from "forge-std/console2.sol";
import { PErc20Delegator } from "peridot/PErc20Delegator.sol";
import { RobinhoodBoostedDelegateV2 } from "../src/RobinhoodBoostedDelegateV2.sol";

/// @notice Reviewed two-stage procedure for the lending-delegate rounding correction.
/// @dev Nothing here runs unless the governor signs locally. Build with
///      `FOUNDRY_PROFILE=lending_candidate`: the candidate does NOT fit EIP-170 under the default
///      or `lending_upgrade` settings, and the creation-code pin below rejects any other build.
abstract contract LendingDelegateUpgradeBase is Script {
    address constant GOVERNOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant PSTOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    address constant PUSDG = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    bytes32 constant ORIGINAL_DELEGATE_CODEHASH =
        0xa6913bd52087e56926b3f17fd131b7f331af194aaa77321e452582e75fb8cc34;
    /// Creation-code hash of RobinhoodBoostedDelegateV2 under `lending_candidate`
    /// (printed by verify_lending_delegate_candidate.py). A different compiler profile or source
    /// change produces a different hash and the script refuses to continue.
    bytes32 constant CANDIDATE_CREATION_CODEHASH =
        0xe6784d3748d868cb39ab2188df50cc480c589872733df83d4a07d98fbe5696e6;
    uint256 constant EIP170_LIMIT = 24_576;
    uint256 constant IMMUTABLE_REFERENCES = 4;
    /// Highest declared storage slot of the delegator + delegate layout (slots 21 to 28 are the
    /// delegate fields: vault, pair, buffer, pause flag, operator, loss counter, delay, queue,
    /// mint flag). verify_lending_delegate_candidate.py asserts the compiled layout stays within it.
    uint256 constant LAST_DECLARED_SLOT = 28;

    function _preflight() internal view {
        require(block.chainid == 4663, "WRONG_CHAIN");
        require(
            keccak256(type(RobinhoodBoostedDelegateV2).creationCode) == CANDIDATE_CREATION_CODEHASH,
            "WRONG_BUILD_USE_LENDING_CANDIDATE_PROFILE"
        );
    }

    function _market(address market) internal view returns (PErc20Delegator m) {
        m = PErc20Delegator(payable(market));
        require(m.admin() == GOVERNOR, "MARKET_ADMIN_CHANGED_USE_CURRENT_GOVERNANCE");
    }

    function _countWord(bytes memory code, bytes32 word) internal pure returns (uint256 n) {
        for (uint256 i; i + 32 <= code.length; i++) {
            bytes32 chunk;
            assembly ("memory-safe") {
                chunk := mload(add(add(code, 0x20), i))
            }
            if (chunk == word) n++;
        }
    }

    /// @dev Structural checks that do not depend on knowing the deployed address in advance.
    function _checkDeployed(address delegate) internal view returns (address module) {
        require(delegate.code.length > 0, "NO_CODE");
        require(delegate.code.length <= EIP170_LIMIT, "OVER_EIP170");
        module = vm.computeCreateAddress(delegate, 1);
        require(module.code.length > 0, "MODULE_NOT_DEPLOYED");
        require(
            _countWord(delegate.code, bytes32(uint256(uint160(module)))) == IMMUTABLE_REFERENCES,
            "MODULE_IMMUTABLE_NOT_SET"
        );
    }
}

/// @notice Stage 1: deploys the candidate (and, via its constructor, its own accounting module).
/// Touches no market. Verify the result offline before stage 2.
contract DeployLendingDelegate is LendingDelegateUpgradeBase {
    function run() external returns (address delegate) {
        _preflight();
        _market(PSTOCK);
        _market(PUSDG);
        require(
            PErc20Delegator(payable(PSTOCK)).implementation().codehash == ORIGINAL_DELEGATE_CODEHASH
                && PErc20Delegator(payable(PUSDG)).implementation().codehash
                    == ORIGINAL_DELEGATE_CODEHASH,
            "MARKETS_NOT_ON_THE_REVIEWED_ORIGINAL"
        );
        vm.startBroadcast(GOVERNOR);
        delegate = address(new RobinhoodBoostedDelegateV2());
        vm.stopBroadcast();
        address module = _checkDeployed(delegate);
        console2.log("delegate", delegate);
        console2.log("module", module);
        console2.log("runtime bytes", delegate.code.length);
        console2.logBytes32(delegate.codehash);
        console2.log(
            "Next: python3 remediation/tools/verify_lending_delegate_deployment.py --delegate <address>"
        );
    }
}

/// @notice Stage 2: installs the verified candidate behind both installed markets.
/// Requires NEW_LENDING_DELEGATE and EXPECTED_LENDING_DELEGATE_CODEHASH, the latter produced by
/// verify_lending_delegate_deployment.py from the compiled artifacts, not from this deployment.
/// Each market is a separate transaction; a market already on the candidate is skipped, so an
/// interrupted run can be re-simulated and repeated safely.
contract InstallLendingDelegate is LendingDelegateUpgradeBase {
    function run() external {
        _preflight();
        address delegate = vm.envAddress("NEW_LENDING_DELEGATE");
        bytes32 expected = vm.envBytes32("EXPECTED_LENDING_DELEGATE_CODEHASH");
        require(delegate.codehash == expected, "CANDIDATE_CODE_MISMATCH");
        _checkDeployed(delegate);
        address[2] memory markets = [PSTOCK, PUSDG];
        for (uint256 i; i < markets.length; i++) {
            PErc20Delegator m = _market(markets[i]);
            address current = m.implementation();
            if (current == delegate) continue;
            require(
                current.codehash == ORIGINAL_DELEGATE_CODEHASH,
                "MARKET_NOT_ON_THE_REVIEWED_ORIGINAL"
            );
            bytes32 before = _fingerprint(markets[i]);
            vm.startBroadcast(GOVERNOR);
            m._setImplementation(delegate, false, "");
            vm.stopBroadcast();
            require(m.implementation() == delegate, "IMPLEMENTATION_NOT_SET");
            require(_fingerprint(markets[i]) == before, "MARKET_STATE_CHANGED_BY_UPGRADE");
        }
    }

    /// @dev Every declared storage slot except the implementation slot (20), plus the economic views.
    /// Because `_becomeImplementation("")` can write delegate fields (it sets `actionDelay` and
    /// `vaultPaused` only when unset), equality of all slots 0 to 28 is what proves it did not.
    function _fingerprint(address market) internal view returns (bytes32 h) {
        h = keccak256(
            abi.encode(
                vm.load(market, bytes32(uint256(0))),
                vm.load(market, bytes32(uint256(19))),
                PErc20Delegator(payable(market)).exchangeRateStored(),
                PErc20Delegator(payable(market)).totalSupply(),
                PErc20Delegator(payable(market)).totalBorrows(),
                PErc20Delegator(payable(market)).totalReserves()
            )
        );
        for (uint256 slot = 1; slot <= LAST_DECLARED_SLOT; slot++) {
            if (slot == 19 || slot == 20) continue;
            h = keccak256(abi.encode(h, vm.load(market, bytes32(slot))));
        }
    }
}
