// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
interface IRewardRecovery {
    function recoverReward(bytes32 id, address token, address receiver, uint256 amount) external;
}
