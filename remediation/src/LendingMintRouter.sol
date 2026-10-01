// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { SafeERC20 } from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

interface ILendingMarket {
    function underlying() external view returns (address);
    function mint(uint256 mintAmount) external returns (uint256);
    function balanceOf(address owner) external view returns (uint256);
}

/// @title LendingMintRouter
/// @notice Supply to one of two fixed Peridot markets with a caller-selected minimum number of
///         pTokens. Exists because the delegate's legacy `mint` has no minimum-received bound and
///         a `mintWithMinShares` entry point does not fit under the contract size limit.
/// @dev Stateless and ownerless: no admin, no upgrade path, no stored balances. Only the two
///      constructor markets are accepted, so a caller cannot make the router approve or call an
///      arbitrary contract. The router never holds tokens between transactions; it pulls the exact
///      amount, mints, checks the share delta, forwards every share to the caller and revokes the
///      approval. It works against both the original and the corrected delegate. Against the
///      original it also rejects zero-share mints, because the minimum is required to be at least 1.
contract LendingMintRouter {
    using SafeERC20 for IERC20;

    address public immutable marketA;
    address public immutable marketB;

    uint256 private _locked = 1;

    modifier nonReentrant() {
        if (_locked != 1) revert Reentrant();
        _locked = 2;
        _;
        _locked = 1;
    }

    error Reentrant();
    error UnsupportedMarket();
    error ZeroAmount();
    error ZeroMinShares();
    error TransferInMismatch();
    error MintFailed(uint256 code);
    error InsufficientShares(uint256 minted, uint256 minimum);
    error ResidualBalance();

    event MintedWithMinimum(
        address indexed market,
        address indexed account,
        uint256 amount,
        uint256 minted,
        uint256 minimum
    );

    constructor(address marketA_, address marketB_) {
        require(marketA_ != marketB_, "SAME_MARKET");
        require(marketA_.code.length > 0 && marketB_.code.length > 0, "NO_CODE");
        marketA = marketA_;
        marketB = marketB_;
    }

    /// @notice Pulls `amount` underlying from the caller, supplies it to `market`, and forwards the
    ///         minted pTokens to the caller, reverting unless at least `minShares` were minted.
    /// @dev The caller must first approve this router for `amount` of the market's underlying.
    function mintWithMinShares(address market, uint256 amount, uint256 minShares)
        external
        nonReentrant
        returns (uint256 minted)
    {
        if (market != marketA && market != marketB) revert UnsupportedMarket();
        if (amount == 0) revert ZeroAmount();
        if (minShares == 0) revert ZeroMinShares();

        IERC20 asset = IERC20(ILendingMarket(market).underlying());
        uint256 assetBefore = asset.balanceOf(address(this));
        asset.safeTransferFrom(msg.sender, address(this), amount);
        // Fee-on-transfer and rebasing tokens would break the exact-amount accounting.
        if (asset.balanceOf(address(this)) - assetBefore != amount) revert TransferInMismatch();

        uint256 sharesBefore = ILendingMarket(market).balanceOf(address(this));
        asset.forceApprove(market, amount);
        uint256 code = ILendingMarket(market).mint(amount);
        asset.forceApprove(market, 0);
        if (code != 0) revert MintFailed(code);

        minted = ILendingMarket(market).balanceOf(address(this)) - sharesBefore;
        if (minted < minShares) revert InsufficientShares(minted, minShares);

        IERC20(market).safeTransfer(msg.sender, minted);
        // Nothing may stay behind: all underlying was consumed and all shares forwarded.
        if (
            asset.balanceOf(address(this)) != assetBefore
                || ILendingMarket(market).balanceOf(address(this)) != sharesBefore
        ) revert ResidualBalance();

        emit MintedWithMinimum(market, msg.sender, amount, minted, minShares);
    }
}
