// SPDX-License-Identifier: BSD-3-Clause
pragma solidity 0.8.26;
import {Unitroller} from "peridot/Unitroller.sol";
import {Peridottroller} from "peridot/Peridottroller.sol";

/// @notice Compound Unitroller layout and dispatch, with atomic timelock ownership at creation.
contract RamsesUnitroller is Unitroller {
    constructor(address timelock, address implementation_) {
        require(timelock.code.length != 0 && implementation_.code.length != 0, "CONTRACT_REQUIRED");
        admin = timelock;
        peridottrollerImplementation = implementation_;
        emit NewAdmin(msg.sender, timelock);
        emit NewImplementation(address(0), implementation_);
    }
}
