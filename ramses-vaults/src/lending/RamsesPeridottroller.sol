// SPDX-License-Identifier: BSD-3-Clause
pragma solidity 0.8.26;
import { Peridottroller } from "peridot/Peridottroller.sol";

/// @notice The captured chain-4663 controller logic, with implementation ownership also locked.
contract RamsesPeridottroller is Peridottroller {
    constructor(address timelock) {
        require(timelock.code.length != 0, "TIMELOCK_REQUIRED");
        admin = timelock;
    }
}
