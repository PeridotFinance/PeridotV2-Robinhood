// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;
import { Script } from "forge-std/Script.sol";

interface IAcceptanceToken {
    function decimals() external view returns (uint8);
    function balanceOf(address) external view returns (uint256);
    function approve(address, uint256) external returns (bool);
}

interface IAcceptanceMarket is IAcceptanceToken {
    function underlying() external view returns (address);
    function mint(uint256) external returns (uint256);
    function borrow(uint256) external returns (uint256);
    function repayBorrow(uint256) external returns (uint256);
    function redeemUnderlying(uint256) external returns (uint256);
    function borrowBalanceStored(address) external view returns (uint256);
}

interface IAcceptanceController {
    function enterMarkets(address[] calldata) external returns (uint256[] memory);
    function exitMarket(address) external returns (uint256);
    function checkMembership(address, address) external view returns (bool);
    function oracle() external view returns (address);
}

interface IAcceptanceGuard {
    function pricesUSD18(bytes32) external view returns (uint256, uint256);
}

/// @notice Small operator acceptance test. No administration, swaps, or allocation changes.
contract LendingAcceptance is Script {
    address constant ACTOR = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    IAcceptanceMarket constant STOCK =
        IAcceptanceMarket(0xa155ccCB986774AE818b3F10F07d01D1b7A47b26);
    IAcceptanceMarket constant DOLLAR =
        IAcceptanceMarket(0x55aEd0569c8f0D166D71facE57B57C2f2624a563);
    IAcceptanceToken constant NVDA = IAcceptanceToken(0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC);
    IAcceptanceToken constant USDG = IAcceptanceToken(0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168);
    IAcceptanceController constant CONTROLLER =
        IAcceptanceController(0x6148183676E304dbe63a85C350c208DA3cEAc39C);
    uint256 constant SUPPLY = 1e15; // 0.001 NVDA
    uint256 constant BORROW = 50_000; // 0.05 USDG
    uint256 constant REPAY_CAP = 51_000; // 0.051 USDG approval; actual debt pulled

    function _prepare() internal {
        require(block.chainid == 4663, "WRONG_CHAIN");
        // Only adjusts local Foundry execution to the independently read native EVM height.
        vm.roll(vm.envUint("ACCEPTANCE_NATIVE_BLOCK"));
        require(
            STOCK.underlying() == address(NVDA) && DOLLAR.underlying() == address(USDG),
            "UNDERLYING_CHANGED"
        );
        require(NVDA.decimals() == 18 && USDG.decimals() == 6, "DECIMALS_CHANGED");
        require(CONTROLLER.oracle() == 0xe4e03C2FdaeF915ACe705D106b2660B1E342A2E4, "ORACLE_CHANGED");
    }

    function _open() internal {
        (uint256 stockPrice, uint256 dollarPrice) = IAcceptanceGuard(
                0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741
            ).pricesUSD18(keccak256("NVDA/USDG"));
        require(stockPrice > 0 && dollarPrice > 0, "PRICE_UNAVAILABLE");
        require(
            STOCK.borrowBalanceStored(ACTOR) == 0 && DOLLAR.borrowBalanceStored(ACTOR) == 0,
            "EXISTING_DEBT"
        );
        require(
            !CONTROLLER.checkMembership(ACTOR, address(STOCK))
                && !CONTROLLER.checkMembership(ACTOR, address(DOLLAR)),
            "EXISTING_MEMBERSHIP_OR_PARTIAL_RUN"
        );
        require(
            NVDA.balanceOf(ACTOR) >= SUPPLY && USDG.balanceOf(ACTOR) >= REPAY_CAP, "WALLET_BALANCE"
        );
        uint256 stockBefore = NVDA.balanceOf(ACTOR);
        uint256 dollarsBefore = USDG.balanceOf(ACTOR);
        uint256 sharesBefore = STOCK.balanceOf(ACTOR);
        vm.startBroadcast(ACTOR);
        require(NVDA.approve(address(STOCK), SUPPLY), "APPROVE_STOCK");
        require(STOCK.mint(SUPPLY) == 0, "MINT_ERROR");
        require(
            NVDA.balanceOf(ACTOR) == stockBefore - SUPPLY && STOCK.balanceOf(ACTOR) > sharesBefore,
            "MINT_DELTA"
        );
        address[] memory markets = new address[](1);
        markets[0] = address(STOCK);
        uint256[] memory errors = CONTROLLER.enterMarkets(markets);
        require(errors.length == 1 && errors[0] == 0, "ENTER_ERROR");
        require(DOLLAR.borrow(BORROW) == 0, "BORROW_ERROR");
        require(
            USDG.balanceOf(ACTOR) == dollarsBefore + BORROW
                && DOLLAR.borrowBalanceStored(ACTOR) == BORROW,
            "BORROW_DELTA"
        );
        vm.stopBroadcast();
    }

    function _close() internal {
        uint256 debt = DOLLAR.borrowBalanceStored(ACTOR);
        require(debt >= BORROW && debt <= REPAY_CAP, "REVIEW_DEBT_OR_PARTIAL_RUN");
        require(STOCK.borrowBalanceStored(ACTOR) == 0, "UNEXPECTED_STOCK_DEBT");
        require(CONTROLLER.checkMembership(ACTOR, address(STOCK)), "SUPPLY_STAGE_REQUIRED");
        uint256 stockBefore = NVDA.balanceOf(ACTOR);
        vm.startBroadcast(ACTOR);
        require(USDG.approve(address(DOLLAR), REPAY_CAP), "APPROVE_REPAY");
        require(DOLLAR.repayBorrow(type(uint256).max) == 0, "REPAY_ERROR");
        require(DOLLAR.borrowBalanceStored(ACTOR) == 0, "DEBT_REMAINS");
        require(USDG.approve(address(DOLLAR), 0), "REVOKE_APPROVAL");
        require(STOCK.redeemUnderlying(SUPPLY) == 0, "REDEEM_ERROR");
        require(NVDA.balanceOf(ACTOR) == stockBefore + SUPPLY, "REDEEM_DELTA");
        require(CONTROLLER.exitMarket(address(DOLLAR)) == 0, "EXIT_DOLLAR_ERROR");
        require(CONTROLLER.exitMarket(address(STOCK)) == 0, "EXIT_STOCK_ERROR");
        vm.stopBroadcast();
    }

    function run() external {
        _prepare();
        _open();
        _close();
    }

    function supplyAndBorrow() external {
        _prepare();
        _open();
    }

    function repayAndRedeem() external {
        _prepare();
        _close();
    }
}
