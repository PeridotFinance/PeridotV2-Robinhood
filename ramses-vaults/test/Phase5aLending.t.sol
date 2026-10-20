// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { LendingFixture } from "./LendingFixture.sol";
import { RamsesBoostedDelegate } from "../src/lending/RamsesBoostedDelegate.sol";
import { RamsesV3PairedAdapter } from "../src/RamsesV3PairedAdapter.sol";
import { RamsesVaultAdapter } from "../src/RamsesVaultAdapter.sol";
import { RamsesOracleGuard } from "../src/RamsesOracleGuard.sol";
import { IRamsesPool, IRamsesNfp, IRamsesGauge } from "../src/interfaces/IRamsesV3.sol";
import { PairConfig, PairLedger } from "baseline/src/libraries/VaultTypes.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { PToken } from "peridot/PToken.sol";
import { EIP20NonStandardInterface } from "peridot/EIP20NonStandardInterface.sol";
import { IERC3156FlashBorrower } from "peridot/PTokenInterfaces.sol";
import { TokenDouble, PoolDouble, GaugeDouble } from "./mocks/RamsesV3Double.sol";

// Isolates an execution loss recognized ONLY by withdrawal, after the initial checkpoint.
contract WithdrawalLossVault {
    address immutable s;
    address immutable u;
    address public immutable liquidityAdapter;
    uint256 immutable sp;
    uint256 immutable up;
    bytes32 constant SLOT = keccak256("phase5a.withdrawal.double");
    constructor(address s_, address u_, address a_, uint256 sp_, uint256 up_) {
        s=s_; u=u_; liquidityAdapter=a_; sp=sp_; up=up_;
    }
    function withdrawn() public view returns (uint256 n) { bytes32 slot=SLOT; assembly { n := sload(slot) } }
    function pairConfig(bytes32) external view returns (PairConfig memory c) { c.stockToken=s; c.usdg=u; c.stockDecimals=18; c.usdgDecimals=6; }
    function ledger(bytes32) external view returns (PairLedger memory l) {
        l.stockPrincipal=sp-(withdrawn()==0 ? 0 : 0.1e18); l.stockIdle=l.stockPrincipal;
        l.usdgPrincipal=up-withdrawn(); l.usdgIdle=l.usdgPrincipal;
    }
    function accountedAssets(bytes32, address token) external view returns (uint256) {
        return token==s ? sp-(withdrawn()==0 ? 0 : 0.1e18) : up-withdrawn();
    }
    function withdrawableAssets(bytes32, address) external view returns (uint256) { return up-withdrawn(); }
    function checkpoint(bytes32, uint256) external {}
    function withdrawForSide(bytes32, address token, uint256 n, address to, uint256) external returns(uint256,uint256) {
        bytes32 slot=SLOT; uint256 next=withdrawn()+n; assembly { sstore(slot,next) }
        IERC20(token).transfer(to,n); return (n,0);
    }
}

contract Phase5aLendingTest is LendingFixture {
    function testPhase5aBorrowChecksHealthAfterWithdrawalLoss() public {
        supply(); enableBorrow();
        PToken[] memory ms = new PToken[](1); ms[0]=PToken(usdgAccount);
        uint256[] memory caps = new uint256[](1); caps[0]=200e6;
        controller._setMarketBorrowCaps(ms,caps);
        WithdrawalLossVault d = new WithdrawalLossVault(address(stock),address(usdg),address(adapter),vault.accountedAssets(PAIR,address(stock)),vault.accountedAssets(PAIR,address(usdg)));
        vm.etch(address(vault),address(d).code);
        uMarket.setVaultPaused(true);
        // Alice owns exactly $200 of collateral, so $160 debt starts at the 80% limit.
        assertEq(sMarket.balanceOfUnderlying(alice),2e18);
        vm.prank(alice);
        vm.expectRevert("POST_CHECKPOINT_SHORTFALL");
        uMarket.borrow(160e6);
        assertEq(uMarket.totalBorrows(),0);
        assertEq(WithdrawalLossVault(address(vault)).withdrawn(),0);
    }

    function testPhase5aRecenterForfeitureSkipsBrokenRewardBookkeeping() public {
        supply(); recenter();
        vm.warp(vm.getBlockTimestamp()+1 hours); fresh(); shift(700);
        vm.mockCallRevert(address(ram),abi.encodeWithSelector(IERC20.balanceOf.selector),"broken reward");
        vm.prank(keeper); vm.expectRevert(); vault.recenter(PAIR,block.timestamp);
        vault.setAllowRewardForfeiture(PAIR,true);
        recenter();
        assertGt(adapter.positionState(PAIR).liquidity,0);
    }

    function testPhase5aRegistrationRejectsGuardPoolMismatch() public {
        // A valid alternate factory pool/gauge must still match this pair's configured guard.
        PoolDouble other = new PoolDouble(pool.token0(),pool.token1(),address(factory),pool.tick());
        GaugeDouble g = new GaugeDouble(other,manager,new address[](0));
        factory.set(address(other),address(g));
        RamsesVaultAdapter a = new RamsesVaultAdapter(address(this),IRamsesNfp(address(manager)),guard);
        vm.expectRevert(RamsesV3PairedAdapter.InvalidConfiguration.selector);
        a.registerPair(PAIR,RamsesV3PairedAdapter.RegisterPairParams(address(stock),address(usdg),IRamsesPool(address(other)),IRamsesGauge(address(g)),400,300));
    }

    function testPhase5aRegistrationRejectsGaugePoolMismatch() public {
        RamsesVaultAdapter a = new RamsesVaultAdapter(address(this),IRamsesNfp(address(manager)),guard);
        vm.mockCall(address(gauge),abi.encodeWithSignature("pool()"),abi.encode(address(7)));
        vm.expectRevert(RamsesV3PairedAdapter.InvalidConfiguration.selector);
        a.registerPair(PAIR,RamsesV3PairedAdapter.RegisterPairParams(address(stock),address(usdg),IRamsesPool(address(pool)),IRamsesGauge(address(gauge)),400,300));
    }

    function testPhase5aDepositMismatchIsAtomicTripwire() public {
        supply();
        uint256 balance=stock.balanceOf(alice);
        uint256 shares=sMarket.balanceOf(alice);
        vm.mockCall(address(vault),abi.encodeWithSignature("depositForPair(bytes32,address,uint256)",PAIR,address(stock)),abi.encode(uint256(1)));
        vm.prank(alice); vm.expectRevert(RamsesBoostedDelegate.DepositBalanceMismatch.selector);
        sMarket.mint(0.1e18);
        assertEq(stock.balanceOf(alice),balance); assertEq(sMarket.balanceOf(alice),shares);
        assertEq(stock.allowance(stockAccount,address(vault)),0);
    }

    function testPhase5aRealDepositFailureDoesNotBlockMintBorrowRedeems() public {
        supply(); enableBorrow();
        vault.setPairPause(PAIR,true,false,false);
        vm.prank(alice); sMarket.mint(0.1e18);
        vm.prank(alice); uMarket.borrow(10e6);
        vm.prank(bob); uMarket.redeemUnderlying(1e6);
        vm.prank(bob); uMarket.redeem(1e8);
        assertEq(usdg.allowance(usdgAccount,address(vault)),0);
        assertEq(stock.allowance(stockAccount,address(vault)),0);
    }

    function testPhase5aSideRebalanceCheckpointsLossBeforeDeposit() public {
        supply(); recenter(); shift(500);
        uint256 lossBefore=vault.ledger(PAIR).cumulativeLossUSDG;
        uint256 preview=uMarket.exchangeRateStored();
        // Donated local cash makes the subsequent maintenance deposit material.
        stock.mint(stockAccount,0.5e18);
        vm.prank(keeper); sMarket.rebalanceVault();
        assertGt(vault.ledger(PAIR).cumulativeLossUSDG,lossBefore);
        assertApproxEqAbs(uMarket.exchangeRateStored(),preview,1e8);
        vm.prank(bob); uMarket.redeemUnderlying(120e6);
        assertLe(uMarket.exchangeRateStored(),preview+1e8);
    }

    function testPhase5aExternalShareAndUnderlyingPaths() public {
        supply(); enableBorrow();
        vm.prank(alice); uMarket.borrow(10e6);
        uMarket.repayBorrowBehalf(alice,1e6);
        vm.prank(alice); usdg.approve(usdgAccount,type(uint256).max);
        vm.prank(alice); uMarket.repayBorrow(1e6);
        vm.prank(bob); uMarket.transfer(alice,1e8);
        vm.prank(bob); uMarket.approve(alice,1e8);
        vm.prank(alice); uMarket.transferFrom(bob,alice,1e8);
        uMarket._addReserves(1e6); uMarket._reduceReserves(1e6);
        vm.expectRevert("PErc20::sweepToken: can not sweep underlying token");
        uMarket.sweepToken(EIP20NonStandardInterface(address(usdg)));
        TokenDouble extra=new TokenDouble("extra",18); extra.mint(usdgAccount,1e18);
        uMarket.sweepToken(EIP20NonStandardInterface(address(extra)));
        assertEq(extra.balanceOf(address(this)),1e18);
        assertEq(uMarket.maxFlashLoan(address(usdg)),0);
        vm.expectRevert("FlashLoan: flash loans are paused");
        uMarket.flashLoan(IERC3156FlashBorrower(address(this)),address(usdg),1,"");
        vm.expectRevert(); sMarket.seize(bob,alice,1e8);
        feed.set(1e8); // real controller liquidation/seize path
        uMarket.liquidateBorrow(alice,1e6,PToken(stockAccount));
        assertGt(sMarket.balanceOf(address(this)),1e8);
    }

    function testPhase5aWeekendSixtyHoursAtCashLimit() public {
        supply(); recenter(); enableBorrow();
        PToken[] memory ms=new PToken[](1); ms[0]=PToken(usdgAccount);
        uint256[] memory caps=new uint256[](1); caps[0]=200e6; controller._setMarketBorrowCaps(ms,caps);
        uint256 cash=usdg.balanceOf(usdgAccount);
        vm.prank(alice); uMarket.borrow(cash);
        vm.warp(vm.getBlockTimestamp()+60 hours); vm.roll(block.number+18000);
        assertEq(uMarket.getCash(),0);
        assertEq(rate.utilization(0,uMarket.totalBorrows(),uMarket.totalReserves()),1e18);
        uint256 maxRate=rate.getBorrowRate(0,1e18,0);
        assertLe(maxRate,5e12);
        uMarket.accrueInterest();
        uint256 debt=uMarket.borrowBalanceStored(alice);
        assertApproxEqAbs(debt,cash+cash*maxRate*18000/1e18,1);
        emit log_named_uint("weekend initial debt USDG raw",cash);
        emit log_named_uint("weekend debt USDG raw",debt);
        emit log_named_uint("maximum borrow rate per native block",maxRate);
        vm.prank(bob); vm.expectRevert(); uMarket.redeemUnderlying(1);
        vm.prank(alice); vm.expectRevert(); sMarket.redeemUnderlying(1);
        vm.expectRevert(); uMarket.liquidateBorrow(alice,1e6,PToken(stockAccount));
        usdg.mint(alice,1e6); vm.prank(alice); usdg.approve(usdgAccount,type(uint256).max);
        vm.prank(alice); uMarket.repayBorrow(type(uint256).max);
        assertEq(uMarket.totalBorrows(),0);
        vm.prank(bob); uMarket.redeemUnderlying(1e6);
    }
}
