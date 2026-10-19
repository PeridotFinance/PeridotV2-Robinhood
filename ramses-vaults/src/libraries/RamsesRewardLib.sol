// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { SafeERC20 } from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { IStrategyLossReserve } from "baseline/src/interfaces/IStrategyLossReserve.sol";
import { RamsesVaultAdapter } from "../RamsesVaultAdapter.sol";
import { RamsesOracleGuard } from "../RamsesOracleGuard.sol";

library RamsesRewardLib {
    using SafeERC20 for IERC20;
    error BalanceDeltaMismatch();
    error RewardSetMismatch();
    event FeesProcessed(
        bytes32 indexed pairId,
        uint256 stockFees,
        uint256 usdgFees,
        uint256 stockReserved,
        uint256 usdgReserved
    );
    event ExternalRewardHeld(bytes32 indexed pairId, address indexed token, uint256 amount);
    event RewardsProcessed(bytes32 indexed pairId, bool freshHarvest);

    function credit(
        PairConfig storage c,
        PairLedger storage l,
        mapping(address => uint256) storage aggregate,
        bytes32 id,
        IStrategyLossReserve reserve,
        uint256 stock,
        uint256 usdg
    ) public {
        uint256 sr = Math.mulDiv(stock, c.reserveFeeBps, 10_000);
        uint256 ur = Math.mulDiv(usdg, c.reserveFeeBps, 10_000);
        _reserve(reserve, id, c.stockToken, sr);
        _reserve(reserve, id, c.usdg, ur);
        l.stockIdle += stock - sr;
        l.stockPrincipal += stock - sr;
        l.usdgIdle += usdg - ur;
        l.usdgPrincipal += usdg - ur;
        aggregate[c.usdg] += usdg - ur;
        emit FeesProcessed(id, stock, usdg, sr, ur);
    }

    function _reserve(IStrategyLossReserve reserve, bytes32 id, address token, uint256 amount)
        private
    {
        if (amount == 0) return;
        IERC20 t = IERC20(token);
        uint256 before_ = t.balanceOf(address(this));
        t.forceApprove(address(reserve), amount);
        uint256 received = reserve.deposit(id, token, amount);
        t.forceApprove(address(reserve), 0);
        if (
            received != amount || t.balanceOf(address(this)) + amount != before_
                || t.allowance(address(this), address(reserve)) != 0
        ) revert BalanceDeltaMismatch();
    }

    struct Ctx {
        bytes32 id;
        RamsesVaultAdapter adapter;
        RamsesOracleGuard guard;
        IStrategyLossReserve reserve;
        uint256 deadline;
        bool fresh;
    }

    function receiveRewards(
        PairConfig storage c,
        PairLedger storage l,
        mapping(address => uint256) storage aggregate,
        mapping(address => uint256) storage externalRewards,
        Ctx memory p
    ) external {
        address[] memory old = p.adapter.rewardTokens(p.id);
        address[] memory live =
            p.fresh ? p.guard.feedConfig(p.id).gauge.getRewardTokens() : new address[](0);
        if (old.length > 8 || live.length > 8) revert RewardSetMismatch();
        address[] memory tokens = new address[](old.length + live.length);
        uint256[] memory before_ = new uint256[](tokens.length);
        for (uint256 i; i < tokens.length; ++i) {
            tokens[i] = i < old.length ? old[i] : live[i - old.length];
            before_[i] = IERC20(tokens[i]).balanceOf(address(this));
        }
        (address[] memory paid, uint256[] memory amounts) =
            p.fresh ? p.adapter.harvest(p.id, p.deadline) : p.adapter.claimPendingRewards(p.id);
        if (paid.length != amounts.length || paid.length > 8) revert RewardSetMismatch();
        uint256 stock;
        uint256 usdg;
        for (uint256 i; i < paid.length; ++i) {
            for (uint256 k; k < i; ++k) {
                if (paid[i] == paid[k]) revert RewardSetMismatch();
            }
            bool found;
            for (uint256 j; j < tokens.length; ++j) {
                if (paid[i] == tokens[j]) {
                    if (IERC20(paid[i]).balanceOf(address(this)) != before_[j] + amounts[i]) {
                        revert BalanceDeltaMismatch();
                    }
                    found = true;
                    break;
                }
            }
            if (!found) revert RewardSetMismatch();
            if (paid[i] == c.stockToken) {
                stock += amounts[i];
            } else if (paid[i] == c.usdg) {
                usdg += amounts[i];
            } else {
                externalRewards[paid[i]] += amounts[i];
                emit ExternalRewardHeld(p.id, paid[i], amounts[i]);
            }
        }
        credit(c, l, aggregate, p.id, p.reserve, stock, usdg);
        emit RewardsProcessed(p.id, p.fresh);
    }
}
