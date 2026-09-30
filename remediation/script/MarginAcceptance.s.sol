// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import { Script } from "forge-std/Script.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { IsolatedMarginTypes as T } from "peridot/margin/IsolatedMarginTypes.sol";

interface IMarginAcceptanceMarket {
    function balanceOf(address) external view returns (uint256);
    function allowance(address, address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
    function exchangeRateCurrent() external returns (uint256);
    function borrowBalanceCurrent(address) external returns (uint256);
    function borrowBalanceStored(address) external view returns (uint256);
}

interface IMarginAcceptanceVault {
    function freeBalance(address, address) external view returns (uint256);
    function lockedBalance(address, address) external view returns (uint256);
    function deposit(address, uint256) external;
    function withdraw(address, uint256) external;
}

interface IMarginAcceptanceConfig {
    function opensPaused() external view returns (bool);
    function openFeeBps() external view returns (uint16);
    function closeFeeBps() external view returns (uint16);
    function getPairRisk(address, address, address) external view returns (T.PairRiskConfig memory);
}

interface IMarginAcceptanceQuoter {
    function quoteOpen(address, address, address, uint256, uint16)
        external
        view
        returns (uint256, uint256);
    function expectedOut(address, address, uint256) external view returns (uint256);
}

interface IMarginAcceptanceFlash {
    function paused() external view returns (bool);
    function flashFee(address, uint256) external view returns (uint256);
}

interface IMarginAcceptanceRisk {
    function getMetrics(address) external view returns (T.AccountMetrics memory);
}

interface IMarginAcceptanceExecutor {
    struct OpenParams {
        address marginPToken;
        address positionPToken;
        address debtPToken;
        uint256 marginPTokenAmount;
        uint16 leverageX100;
        uint256 maxOpeningFeePToken;
        uint256 minPositionUnderlying;
        T.Side side;
        bytes swapData;
    }

    struct CloseParams {
        uint256 positionId;
        uint16 closeBps;
        uint256 maxClosingFeePToken;
        uint256 minDebtUnderlying;
        uint256 minMarginUnderlying;
        bytes positionToDebtSwapData;
        bytes debtToMarginSwapData;
    }
    function openPosition(OpenParams calldata) external returns (uint256);
    function closePosition(CloseParams calldata) external returns (uint256);
    function positions(uint256) external view returns (T.Position memory);
}

/// @notice Operator-only acceptance scripts. Existing pUSDG collateral; no governance changes.
contract MarginAcceptance is Script {
    address constant ACTOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant USD = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    address constant STOCK = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address constant PUSD = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    address constant PSTOCK = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    IMarginAcceptanceVault constant VAULT =
        IMarginAcceptanceVault(0x04D4A5555b7a37017A67B4D21A1Da5838de28B9e);
    IMarginAcceptanceExecutor constant EXECUTOR =
        IMarginAcceptanceExecutor(0x6A45Ae86bD992d250580d08D340A06A04D478977);
    IMarginAcceptanceConfig constant CONFIG =
        IMarginAcceptanceConfig(0x09F94fe0B79E000c8a26617c63E3427fdECB528b);
    IMarginAcceptanceQuoter constant QUOTER =
        IMarginAcceptanceQuoter(0xeD3c353Ab237329BD53CC7eB24E66B370155FE6e);
    IMarginAcceptanceFlash constant FLASH =
        IMarginAcceptanceFlash(0x79d33c9BbC1D0711e88C5602f86135Ab4C088b06);
    IMarginAcceptanceRisk constant RISK =
        IMarginAcceptanceRisk(0xC8b178C3c74570472FF1eeE0DD559e61AF9f9678);
    uint256 constant SHARES = 1_000_000_000; // 10 pUSDG, about 0.20 USDG underlying
    uint256 constant MIN_RETURN = 180_000; // 0.18 USDG, explicit operator close floor
    uint16 constant LEVERAGE = 200;

    function _prepare() internal {
        require(block.chainid == 4663, "WRONG_CHAIN");
        vm.roll(vm.envUint("MARGIN_ACCEPTANCE_NATIVE_BLOCK"));
        require(CONFIG.openFeeBps() == 0 && CONFIG.closeFeeBps() == 0, "REVIEW_CHANGED_FEES");
    }

    function _short() internal view returns (bool) {
        return vm.envBool("MARGIN_ACCEPTANCE_SHORT");
    }

    function _position(uint256 id) internal view returns (T.Position memory p) {
        p = EXECUTOR.positions(id);
        require(p.owner == ACTOR && p.id == id && p.account.code.length > 0, "POSITION_IDENTITY");
        require(
            p.marginPToken == PUSD && p.positionPToken == (_short() ? PUSD : PSTOCK)
                && p.debtPToken == (_short() ? PSTOCK : PUSD)
                && p.side == (_short() ? T.Side.SHORT : T.Side.LONG),
            "POSITION_DIRECTION"
        );
    }

    function _open() internal returns (uint256 id) {
        require(!CONFIG.opensPaused() && !FLASH.paused(), "OPENS_UNAVAILABLE");
        require(
            VAULT.freeBalance(ACTOR, PUSD) == 0 && VAULT.lockedBalance(ACTOR, PUSD) == 0,
            "EXISTING_MARGIN_OR_PARTIAL_RUN"
        );
        require(
            IMarginAcceptanceMarket(PUSD).allowance(ACTOR, address(VAULT)) == 0,
            "EXISTING_ALLOWANCE"
        );
        require(IMarginAcceptanceMarket(PUSD).balanceOf(ACTOR) >= SHARES, "INSUFFICIENT_SHARES");
        address position = _short() ? PUSD : PSTOCK;
        address debt = _short() ? PSTOCK : PUSD;
        T.PairRiskConfig memory risk = CONFIG.getPairRisk(PUSD, position, debt);
        require(
            risk.enabled && risk.maxLeverageX100 == 500 && risk.maxPositionValueUsd == 2e18
                && risk.maxDebtValueUsd == 1e18 && risk.maxSlippageBps == 100
                && risk.oracleDeviationBps == 100,
            "RISK_CHANGED"
        );
        uint256 rate = IMarginAcceptanceMarket(PUSD).exchangeRateCurrent();
        IMarginAcceptanceMarket(PSTOCK).exchangeRateCurrent();
        uint256 margin = Math.mulDiv(SHARES, rate, 1e18);
        require(margin >= 190_000 && margin <= 210_000, "REVIEW_SHARE_VALUE");
        (, uint256 minimum) = QUOTER.quoteOpen(PUSD, position, debt, margin, LEVERAGE);
        require(minimum > 0, "ZERO_MINIMUM");
        vm.startBroadcast(ACTOR);
        require(IMarginAcceptanceMarket(PUSD).approve(address(VAULT), SHARES), "APPROVE");
        VAULT.deposit(PUSD, SHARES);
        require(IMarginAcceptanceMarket(PUSD).approve(address(VAULT), 0), "REVOKE");
        id = EXECUTOR.openPosition(
            IMarginAcceptanceExecutor.OpenParams(
                PUSD,
                position,
                debt,
                SHARES,
                LEVERAGE,
                0,
                minimum,
                _short() ? T.Side.SHORT : T.Side.LONG,
                ""
            )
        );
        vm.stopBroadcast();
        T.Position memory p = _position(id);
        T.AccountMetrics memory metrics = RISK.getMetrics(p.account);
        require(
            p.status == T.Status.ACTIVE && metrics.debtValueUsd > 0 && metrics.debtValueUsd <= 1e18
                && metrics.grossAssetValueUsd <= 2e18 && metrics.healthFactorBps > 10000
                && metrics.leverageX100 <= LEVERAGE,
            "OPEN_POSTCONDITION"
        );
        require(
            VAULT.freeBalance(ACTOR, PUSD) == 0 && VAULT.lockedBalance(ACTOR, PUSD) == SHARES,
            "CUSTODY_POSTCONDITION"
        );
    }

    function _close(uint256 id) internal {
        T.Position memory p = _position(id);
        require(p.status == T.Status.ACTIVE, "POSITION_NOT_ACTIVE");
        uint256 rate = IMarginAcceptanceMarket(p.positionPToken).exchangeRateCurrent();
        uint256 debt = IMarginAcceptanceMarket(p.debtPToken).borrowBalanceCurrent(p.account);
        uint256 assets =
            Math.mulDiv(IMarginAcceptanceMarket(p.positionPToken).balanceOf(p.account), rate, 1e18);
        address positionAsset = _short() ? USD : STOCK;
        address debtAsset = _short() ? STOCK : USD;
        uint256 minimumDebt =
            Math.mulDiv(QUOTER.expectedOut(positionAsset, debtAsset, assets), 9900, 10000);
        // Long residual is already USDG, so its margin floor must be included in the first swap.
        uint256 requiredResidual;
        if (_short()) {
            // Zero on the second swap selects its runtime-computed protocol floor (1%).
            // Enforce the absolute USDG return floor through the FIRST swap's minimum:
            // reserve enough NVDA for that USDG floor even after the second leg's full 1%.
            uint256 requiredDollarValue = Math.mulDiv(MIN_RETURN, 10000, 9900, Math.Rounding.Ceil);
            requiredResidual = QUOTER.expectedOut(USD, STOCK, requiredDollarValue) + 1;
        } else {
            requiredResidual = MIN_RETURN;
        }
        minimumDebt =
            Math.max(minimumDebt, debt + FLASH.flashFee(debtAsset, debt) + requiredResidual);
        require(minimumDebt > 0 && debt > 0, "REVIEW_CLOSE_QUOTE");
        vm.startBroadcast(ACTOR);
        EXECUTOR.closePosition(
            IMarginAcceptanceExecutor.CloseParams(
                id, 10000, 0, minimumDebt, _short() ? 0 : MIN_RETURN, "", ""
            )
        );
        vm.stopBroadcast();
        require(_position(id).status == T.Status.CLOSED, "NOT_CLOSED");
        require(
            IMarginAcceptanceMarket(PUSD).borrowBalanceStored(p.account) == 0
                && IMarginAcceptanceMarket(PSTOCK).borrowBalanceStored(p.account) == 0,
            "RESIDUAL_DEBT"
        );
        require(
            VAULT.lockedBalance(ACTOR, PUSD) == 0 && VAULT.freeBalance(ACTOR, PUSD) > 0,
            "CLOSE_CUSTODY"
        );
    }

    function _withdraw(uint256 id) internal {
        require(_position(id).status == T.Status.CLOSED, "CLOSE_FIRST");
        require(VAULT.lockedBalance(ACTOR, PUSD) == 0, "MARGIN_STILL_LOCKED");
        uint256 amount = VAULT.freeBalance(ACTOR, PUSD);
        require(amount > 0, "NOTHING_TO_WITHDRAW");
        uint256 before = IMarginAcceptanceMarket(PUSD).balanceOf(ACTOR);
        vm.startBroadcast(ACTOR);
        VAULT.withdraw(PUSD, amount);
        vm.stopBroadcast();
        require(
            VAULT.freeBalance(ACTOR, PUSD) == 0
                && IMarginAcceptanceMarket(PUSD).balanceOf(ACTOR) == before + amount,
            "WITHDRAW_POSTCONDITION"
        );
    }

    function run() external {
        _prepare();
        uint256 id = _open();
        _close(id);
        _withdraw(id);
    }

    function openTest() external {
        _prepare();
        _open();
    }

    function closeTest() external {
        _prepare();
        _close(vm.envUint("MARGIN_ACCEPTANCE_POSITION_ID"));
    }

    function withdrawTest() external {
        _prepare();
        _withdraw(vm.envUint("MARGIN_ACCEPTANCE_POSITION_ID"));
    }
}
