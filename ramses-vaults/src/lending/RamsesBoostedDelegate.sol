// SPDX-License-Identifier: BSD-3-Clause
pragma solidity ^0.8.20;

import "peridot/PErc20Delegate.sol";
import {RamsesMarketAccounting} from "./RamsesMarketAccounting.sol";
import "peridot/interfaces/IRobinhoodBoostedVault.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/// @notice Separate Ramses markets; derived from the corrected V2 delegate.
/// @dev New deployments only. Governance setters rely on the external timelock.
contract RamsesBoostedDelegate is PErc20Delegate {
    using SafeERC20 for IERC20;

    error DepositBalanceMismatch();
    error InvalidBuffer();
    error InvalidOperator();
    error InvalidPair();
    error InvalidVault();
    error MarketNotFresh();
    error OnlyAdmin();
    error OnlyVaultOperator();
    error OldVaultNotEmpty();
    error PTokenNotSideAccount();
    error StrategyLiquidityShortfall();
    error WithdrawalBalanceMismatch();
    error ZeroSharesMinted();

    uint256 internal constant MANTISSA_ONE = 1e18;
    uint256 internal constant MAX_SETTLEMENT_STEPS = 3;

    IRobinhoodBoostedVault public robinhoodVault;
    bytes32 public robinhoodPairId;

    /// @notice Fraction of managed underlying retained locally, scaled by 1e18.
    uint256 public vaultBufferMantissa;

    /// @notice Blocks new vault deposits. Existing vault claims remain part of exchange-rate accounting.
    bool public vaultPaused;

    /// @notice Address allowed to rebalance or deliberately realize a vault checkpoint/loss.
    address public vaultOperator;

    uint256 public cumulativeVaultLoss;
    uint256 public supplyCap;
    uint256 public constant DEAD_SHARES = 1e8;
    address public constant DEAD = address(0xdead);

    /// @dev Requires a live accounted-assets report while a mint prices new pTokens.
    bool internal mintVaultAssetsValidated;

    event RobinhoodVaultConfigured(
        address indexed vault, bytes32 indexed pairId, uint256 bufferMantissa, address indexed operator
    );
    event VaultBufferUpdated(uint256 previousMantissa, uint256 newMantissa);
    event VaultPausedUpdated(bool paused);
    event VaultOperatorUpdated(address indexed previousOperator, address indexed newOperator);
    event VaultDeposit(uint256 requested, uint256 deposited);
    event VaultWithdrawal(uint256 requested, uint256 returned, uint256 realizedLoss);
    event VaultDepositFailed(uint256 requested);
    event VaultWithdrawalFailed(uint256 requested);

    modifier onlyVaultOperator() {
        if (msg.sender != admin && msg.sender != vaultOperator) revert OnlyVaultOperator();
        _;
    }

    /**
     * @notice Initializes delegate-specific storage when installed behind PErc20Delegator.
     * @dev Empty data is the normal deployment path. Governance registers the production
     *      pair with the delegator as that side's account, then configures this delegate.
     */
    function _becomeImplementation(bytes memory data) public override {
        if (msg.sender != admin) revert OnlyAdmin();
        if (address(robinhoodVault) == address(0)) {
            vaultPaused = true;
        }
        if (data.length != 0) {
            (address vault_, bytes32 pairId_, uint256 buffer_, address operator_) =
                abi.decode(data, (address, bytes32, uint256, address));
            _configureVault(vault_, pairId_, buffer_, operator_);
        }
    }

    /**
     * @notice Returns all locally held and vault-accounted underlying.
     */
    function totalManagedAssets() external view returns (uint256) {
        return super.getCashPrior() + _vaultAccountedAssets();
    }

    function vaultAccountedAssets() external view returns (uint256) {
        return _vaultAccountedAssets();
    }

    function vaultLiquidAssets() external view returns (uint256) {
        return _vaultLiquidAssets();
    }

    /**
     * @notice Vault-held underlying that can actually be released right now.
     * @dev Falls to zero while LP liquidity is open and the oracle guard is unhealthy. The
     *      gap against `vaultLiquidAssets` is the amount held in custody but unreachable.
     */
    function vaultWithdrawableAssets() external view returns (uint256) {
        return _vaultWithdrawableAssets();
    }

    /**
     * @notice Supplies underlying only when the vault claim used for mint pricing is live.
     * @dev Live loss previews prevent dilution. Only the named stale-feed error permits
     *      cached claims for unencumbered local-cash redemptions.
     */
    function mint(uint256 mintAmount) external override returns (uint256) {
        _checkpoint(false);
        require(mintAmount != 0 && super.getCashPrior() + _vaultAccountedAssets() + totalBorrows - totalReserves + mintAmount <= supplyCap, "SUPPLY_CAP");
        bool first = totalSupply == 0;
        if (first) require(msg.sender == admin, "TIMELOCK_SEED_REQUIRED");
        uint256 sharesBefore = accountTokens[msg.sender];
        mintVaultAssetsValidated = true;
        mintInternal(mintAmount);
        mintVaultAssetsValidated = false;
        // mintInternal holds the shared nonReentrant lock that every other balance-changing path
        // also takes, so this delta is exactly the pTokens minted by this call.
        if (accountTokens[msg.sender] == sharesBefore) revert ZeroSharesMinted();
        if (first) {
            require(accountTokens[msg.sender] > DEAD_SHARES, "SEED_TOO_SMALL");
            accountTokens[msg.sender] -= DEAD_SHARES;
            accountTokens[DEAD] += DEAD_SHARES;
            emit Transfer(msg.sender, DEAD, DEAD_SHARES);
        }
        return NO_ERROR;
    }

    /**
     * @dev Cash checks use local underlying plus only the vault balance releasable in this
     *      block. Vault idle is not unconditionally reachable: while LP liquidity is open,
     *      every principal reduction routes through the oracle-guarded settle path, so idle
     *      sitting behind an unhealthy oracle is custody, not cash. Counting it here would
     *      understate utilization, underprice the borrow rate, and let borrows and redeems
     *      pass the controller's cash check and then revert in `doTransferOut`.
     */
    function getCashPrior() internal view override returns (uint256) {
        return super.getCashPrior() + _vaultWithdrawableAssets();
    }

    /**
     * @dev Exchange-rate accounting includes the full side-specific vault claim.
     */
    function exchangeRateStoredInternal() internal view override returns (uint256) {
        if (totalSupply == 0) {
            return initialExchangeRateMantissa;
        }
        uint256 managedCash = super.getCashPrior() + _vaultAccountedAssets();
        uint256 netAssets = managedCash + totalBorrows - totalReserves;
        return (netAssets * expScale) / totalSupply;
    }

    function doTransferIn(address from, uint256 amount) internal override returns (uint256) {
        uint256 actual = super.doTransferIn(from, amount);
        // Repayment must remain possible during oracle/strategy failures.
        if (mintVaultAssetsValidated) _rebalanceVault(false);
        return actual;
    }

    function doTransferOut(address payable to, uint256 amount) internal override {
        _checkpoint(false);
        // borrowFresh has already recorded the new debt; check health again after checkpoint.
        (uint256 err,,uint256 shortfall) = IRamsesLiquidity(address(peridottroller)).getAccountLiquidity(to);
        require(err == 0 && shortfall == 0, "POST_CHECKPOINT_SHORTFALL");
        _prepareFixedLiquidity(amount);
        if (super.getCashPrior() < amount) revert StrategyLiquidityShortfall();
        super.doTransferOut(to, amount);
        _rebalanceVault(false);
    }

    /**
     * @notice Redeems pTokens using the post-vault-operation exchange rate.
     * @dev If a vault withdrawal recognizes a loss, the output is recalculated before
     *      token burning and transfer so the loss is shared by all suppliers atomically.
     */
    function redeem(uint256 redeemTokens) external override nonReentrant returns (uint256) {
        _checkpoint(true);
        accrueInterest();
        _redeemTokensLossAware(payable(msg.sender), redeemTokens);
        return NO_ERROR;
    }

    /**
     * @notice Redeems an exact underlying amount using the post-vault-operation exchange rate.
     * @dev A newly recognized loss increases the number of pTokens burned for the exact output.
     */
    function redeemUnderlying(uint256 redeemAmount) external override nonReentrant returns (uint256) {
        _checkpoint(true);
        accrueInterest();
        _redeemUnderlyingLossAware(payable(msg.sender), redeemAmount);
        return NO_ERROR;
    }

    function rebalanceVault() external onlyVaultOperator nonReentrant {
        _checkpoint(false);
        _rebalanceVault(true);
        _requireBuffer();
    }

    /**
     * @notice Pulls vault assets into local pToken cash and records any realized loss.
     * @param amount Amount requested. type(uint256).max requests the full accounted claim.
     */
    function syncVault(uint256 amount)
        external
        onlyVaultOperator
        nonReentrant
        returns (uint256 returned, uint256 realizedLoss)
    {
        uint256 accounted = _vaultAccountedAssets();
        uint256 requested = amount == type(uint256).max ? accounted : amount;
        bool success;
        (returned, realizedLoss, success) = _withdrawFromVault(requested);
        if (requested != 0 && !success) revert StrategyLiquidityShortfall();
    }

    function setVaultConfig(address vault_, bytes32 pairId_, uint256 buffer_, address operator_) external {
        if (msg.sender != admin) revert OnlyAdmin();
        _configureVault(vault_, pairId_, buffer_, operator_);
    }

    function setVaultBuffer(uint256 value) external {
        if (msg.sender != admin) revert OnlyAdmin();
        if (value < 0.5e18 || value > MANTISSA_ONE) revert InvalidBuffer();
        uint256 previous = vaultBufferMantissa;
        vaultBufferMantissa = value;
        _checkpoint(false);
        _rebalanceVault(true);
        _requireBuffer();
        emit VaultBufferUpdated(previous, value);
    }

    function setSupplyCap(uint256 value) external {
        if (msg.sender != admin) revert OnlyAdmin();
        supplyCap = value; // zero means CLOSED, unlike Compound's zero borrow cap.
    }

    function setVaultPaused(bool pause) external {
        if (msg.sender != admin) revert OnlyAdmin();
        vaultPaused = pause;
        emit VaultPausedUpdated(pause);
    }

    function _requireBuffer() internal view {
        uint256 cash = super.getCashPrior();
        require(cash >= ((cash + _vaultAccountedAssets()) * vaultBufferMantissa + 1e18 - 1) / 1e18, "BUFFER_SHORTFALL");
    }

    function _checkpoint(bool allowStale) internal {
        if (_vaultConfigured()) RamsesMarketAccounting.checkpoint(address(robinhoodVault), robinhoodPairId, allowStale);
    }

    function _redeemTokensLossAware(address payable redeemer, uint256 redeemTokens) internal {
        if (accrualBlockNumber != getBlockNumber()) revert MarketNotFresh();

        uint256 redeemAmount;
        for (uint256 i = 0; i < MAX_SETTLEMENT_STEPS; i++) {
            Exp memory exchangeRate = Exp({mantissa: exchangeRateStoredInternal()});
            redeemAmount = mul_ScalarTruncate(exchangeRate, redeemTokens);
            uint256 localCash = super.getCashPrior();
            if (localCash >= redeemAmount) break;
            (uint256 returned, uint256 loss, bool success) = _withdrawFromVault(redeemAmount - localCash);
            if (!success || (returned == 0 && loss == 0)) break;
        }

        Exp memory settledRate = Exp({mantissa: exchangeRateStoredInternal()});
        redeemAmount = mul_ScalarTruncate(settledRate, redeemTokens);
        if (super.getCashPrior() < redeemAmount) revert RedeemTransferOutNotPossible();

        uint256 allowed = peridottroller.redeemAllowed(address(this), redeemer, redeemTokens);
        if (allowed != 0) revert RedeemPeridottrollerRejection(allowed);

        totalSupply -= redeemTokens;
        accountTokens[redeemer] -= redeemTokens;
        super.doTransferOut(redeemer, redeemAmount);
        _rebalanceVault(false);

        emit Transfer(redeemer, address(this), redeemTokens);
        emit Redeem(redeemer, redeemAmount, redeemTokens);
        peridottroller.redeemVerify(address(this), redeemer, redeemAmount, redeemTokens);
    }

    function _redeemUnderlyingLossAware(address payable redeemer, uint256 redeemAmount) internal {
        if (accrualBlockNumber != getBlockNumber()) revert MarketNotFresh();
        _prepareFixedLiquidity(redeemAmount);
        if (super.getCashPrior() < redeemAmount) revert RedeemTransferOutNotPossible();

        // Round burned shares UP at the settled rate, so an exact output is never paid for with
        // less than its value in pTokens. Ceiling division, written out because the library
        // helpers do not fit under EIP-170.
        uint256 settledRate = exchangeRateStoredInternal();
        uint256 redeemTokens = (redeemAmount * expScale + settledRate - 1) / settledRate;
        uint256 allowed = peridottroller.redeemAllowed(address(this), redeemer, redeemTokens);
        if (allowed != 0) revert RedeemPeridottrollerRejection(allowed);

        totalSupply -= redeemTokens;
        accountTokens[redeemer] -= redeemTokens;
        super.doTransferOut(redeemer, redeemAmount);
        _rebalanceVault(false);

        emit Transfer(redeemer, address(this), redeemTokens);
        emit Redeem(redeemer, redeemAmount, redeemTokens);
        peridottroller.redeemVerify(address(this), redeemer, redeemAmount, redeemTokens);
    }

    function _prepareFixedLiquidity(uint256 target) internal {
        for (uint256 i = 0; i < MAX_SETTLEMENT_STEPS; i++) {
            uint256 localCash = super.getCashPrior();
            if (localCash >= target) return;
            (uint256 returned, uint256 loss, bool success) = _withdrawFromVault(target - localCash);
            if (!success || (returned == 0 && loss == 0)) return;
        }
    }

    function _rebalanceVault(bool allowWithdrawal) internal {
        if (vaultPaused || !_vaultConfigured()) return;
        uint256 localCash = super.getCashPrior();
        (bool accountedValid, uint256 accounted) = _tryVaultAccountedAssets();
        (bool liquidValid,) = _tryVaultLiquidAssets();
        if (!accountedValid || !liquidValid) return;
        uint256 totalAssets = localCash + accounted;
        if (totalAssets == 0) return;

        uint256 targetBuffer = (totalAssets * vaultBufferMantissa + MANTISSA_ONE - 1) / MANTISSA_ONE;
        if (localCash > targetBuffer) {
            _depositToVault(localCash - targetBuffer);
        } else if (allowWithdrawal && targetBuffer > localCash) {
            _withdrawFromVault(targetBuffer - localCash);
        }
    }

    function _depositToVault(uint256 amount) internal {
        if (amount == 0 || vaultPaused || !_vaultConfigured()) return;
        IERC20 token = IERC20(underlying);
        address vaultAddress = address(robinhoodVault);
        uint256 balanceBefore = token.balanceOf(address(this));
        token.forceApprove(vaultAddress, amount);
        try robinhoodVault.depositForPair(robinhoodPairId, underlying, amount) returns (uint256 deposited) {
            token.forceApprove(vaultAddress, 0);
            uint256 balanceAfter = token.balanceOf(address(this));
            if (balanceAfter > balanceBefore || balanceBefore - balanceAfter != amount || deposited != amount) {
                revert DepositBalanceMismatch();
            }
            emit VaultDeposit(amount, deposited);
        } catch {
            token.forceApprove(vaultAddress, 0);
            emit VaultDepositFailed(amount);
        }
    }

    function _withdrawFromVault(uint256 requested)
        internal
        returns (uint256 returned, uint256 realizedLoss, bool success)
    {
        if (requested == 0 || !_vaultConfigured()) return (0, 0, false);
        (bool accountedValid, uint256 accounted) = _tryVaultAccountedAssets();
        if (!accountedValid) return (0, 0, false);
        if (requested > accounted) requested = accounted;
        if (requested == 0) return (0, 0, false);

        IERC20 token = IERC20(underlying);
        uint256 balanceBefore = token.balanceOf(address(this));
        try robinhoodVault.withdrawForSide(
            robinhoodPairId, underlying, requested, address(this), block.timestamp
        ) returns (
            uint256 actualReturned, uint256 actualLoss
        ) {
            uint256 balanceAfter = token.balanceOf(address(this));
            if (balanceAfter < balanceBefore || balanceAfter - balanceBefore != actualReturned) {
                revert WithdrawalBalanceMismatch();
            }
            returned = actualReturned;
            realizedLoss = actualLoss;
            cumulativeVaultLoss += actualLoss;
            success = true;
            emit VaultWithdrawal(requested, actualReturned, actualLoss);
        } catch {
            emit VaultWithdrawalFailed(requested);
        }
    }

    function _configureVault(address vault_, bytes32 pairId_, uint256 buffer_, address operator_) internal {
        if (buffer_ < 0.5e18 || buffer_ > MANTISSA_ONE) revert InvalidBuffer();
        if (operator_ == address(0)) revert InvalidOperator();
        if (_vaultConfigured() && (vault_ != address(robinhoodVault) || pairId_ != robinhoodPairId)) {
            if (robinhoodVault.accountedAssets(robinhoodPairId, underlying) != 0) revert OldVaultNotEmpty();
        }
        _validateVault(vault_, pairId_);
        robinhoodVault = IRobinhoodBoostedVault(vault_);
        robinhoodPairId = pairId_;
        vaultBufferMantissa = buffer_;
        vaultOperator = operator_;
        vaultPaused = true;
        emit RobinhoodVaultConfigured(vault_, pairId_, buffer_, operator_);
        emit VaultPausedUpdated(true);
    }

    function _validateVault(address vault_, bytes32 pairId_) internal view {
        if (vault_ == address(0) || vault_.code.length == 0) revert InvalidVault();
        if (pairId_ == bytes32(0)) revert InvalidPair();
        IRobinhoodBoostedVault candidate = IRobinhoodBoostedVault(vault_);
        if (candidate.sideAccount(pairId_, underlying) != address(this)) revert PTokenNotSideAccount();
        candidate.accountedAssets(pairId_, underlying);
        candidate.liquidAssets(pairId_, underlying);
    }

    function _vaultAccountedAssets() internal view returns (uint256) {
        if (!_vaultConfigured()) return 0;
        return RamsesMarketAccounting.assets(address(robinhoodVault), robinhoodPairId, underlying);
    }

    function _vaultLiquidAssets() internal view returns (uint256) {
        if (!_vaultConfigured()) return 0;
        (bool valid, uint256 assets) = _tryVaultLiquidAssets();
        return valid ? assets : 0;
    }

    function _tryVaultAccountedAssets() internal view returns (bool valid, uint256 assets) {
        try robinhoodVault.accountedAssets(robinhoodPairId, underlying) returns (uint256 value) {
            return (true, value);
        } catch {
            return (false, 0);
        }
    }

    function _tryVaultLiquidAssets() internal view returns (bool valid, uint256 assets) {
        try robinhoodVault.liquidAssets(robinhoodPairId, underlying) returns (uint256 value) {
            return (true, value);
        } catch {
            return (false, 0);
        }
    }

    function _vaultWithdrawableAssets() internal view returns (uint256) {
        if (!_vaultConfigured()) return 0;
        (bool valid, uint256 assets) = _tryVaultWithdrawableAssets();
        return valid ? assets : 0;
    }

    function _tryVaultWithdrawableAssets() internal view returns (bool valid, uint256 assets) {
        try robinhoodVault.withdrawableAssets(robinhoodPairId, underlying) returns (uint256 value) {
            return (true, value);
        } catch {
            return (false, 0);
        }
    }

    function _vaultConfigured() internal view returns (bool) {
        return address(robinhoodVault) != address(0) && robinhoodPairId != bytes32(0);
    }

}

interface IRamsesLiquidity {
    function getAccountLiquidity(address) external view returns (uint256, uint256, uint256);
}
