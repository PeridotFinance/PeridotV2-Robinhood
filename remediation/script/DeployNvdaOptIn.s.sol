// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

// Import order matters: PErc20 first, or solc reports a base-before-derived error in PToken.sol.
import "peridot/PErc20.sol";
import { PeridottrollerInterface } from "peridot/PeridottrollerInterface.sol";
import { InterestRateModel } from "peridot/InterestRateModel.sol";
import { PErc20Delegator } from "peridot/PErc20Delegator.sol";
import { Script } from "forge-std/Script.sol";
import { console2 } from "forge-std/console2.sol";
import { RobinhoodLendingPriceAdapterN } from "../src/RobinhoodLendingPriceAdapterN.sol";

interface IOptInMarket {
    function admin() external view returns (address);
    function implementation() external view returns (address);
    function underlying() external view returns (address);
    function peridottroller() external view returns (address);
    function interestRateModel() external view returns (address);
    function decimals() external view returns (uint8);
    function symbol() external view returns (string memory);
    function totalSupply() external view returns (uint256);
    function totalBorrows() external view returns (uint256);
    function exchangeRateStored() external view returns (uint256);
    function vaultPaused() external view returns (bool);
    function actionDelay() external view returns (uint256);
    function robinhoodVault() external view returns (address);
}

/// @notice NVDA opt-in vault markets: creations by a THROWAWAY sender only (no Ledger signature, no admin power kept).
///   NVDA_OPTIN_ACTION=deploy (default): creates upNVDA and upUSDG (PErc20Delegator on the LIVE pNVDA/pUSDG
///     implementation 0x0C6F, admin = Ledger, empty become-data so the vault is unconfigured and paused) and the
///     5-market controller oracle RobinhoodLendingPriceAdapterN(0x266F, [pNVDA, pUSDG, pSPY, upNVDA, upUSDG]).
///     Writes remediation/nvda-optin/journals/<NVDA_OPTIN_JOURNAL>.json.
///   NVDA_OPTIN_ACTION=verify: read-only checks of a journal against chain state (run before every ceremony phase).
/// Nothing here lists, configures or funds anything; the Ledger does that in tools/nvda-optin/ceremony.sh.
contract DeployNvdaOptIn is Script {
    address constant LEDGER = 0x064eFE76812F5f18Bd1aA9708739Fb33C810B2dD;
    address constant OLD_SIGNER = 0x94696d767e65a75581145646960FA0eC886cE5d2;
    address constant CONTROLLER = 0x6148183676E304dbe63a85C350c208DA3cEAc39C;
    address constant LENDING_SOURCE = 0x266F014d1325774F1190f963Df4369E07dDA1d33;
    address constant DELEGATE = 0x0C6F6962d80390F6104f80811a150e8233bd8FF1;
    address constant IRM = 0x0987154fB5676a8Ea545AAf41F8ef2492F785d22;
    address constant PNVDA = 0xa155ccCB986774AE818b3F10F07d01D1b7A47b26;
    address constant PUSDG = 0x55aEd0569c8f0D166D71facE57B57C2f2624a563;
    address constant PSPY = 0xa6660499a9cB9B6691247fE1FAbc248f502E5fB3;
    address constant NVDA = 0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC;
    address constant USDG = 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168;
    uint256 constant STOCK_RATE = 2e26; // 0.02 NVDA per 8-decimal share, as pNVDA
    uint256 constant DOLLAR_RATE = 2e14; // 0.02 USDG per 8-decimal share, as pUSDG

    function run() external {
        require(block.chainid == 4663, "WRONG_CHAIN");
        string memory action = vm.envOr("NVDA_OPTIN_ACTION", string("deploy"));
        string memory path = _path();
        if (keccak256(bytes(action)) == keccak256("verify")) {
            _verify(path);
            return;
        }
        require(keccak256(bytes(action)) == keccak256("deploy"), "UNKNOWN_ACTION");
        require(msg.sender != LEDGER && msg.sender != OLD_SIGNER, "USE_A_THROWAWAY_SENDER");
        require(DELEGATE.code.length > 0 && IRM.code.length > 0 && PSPY.code.length > 0, "MISSING_CODE");
        require(IOptInMarket(PNVDA).implementation() == DELEGATE, "PNVDA_IMPL_CHANGED");
        require(IOptInMarket(PUSDG).implementation() == DELEGATE, "PUSDG_IMPL_CHANGED");

        vm.startBroadcast();
        PErc20Delegator upNvda = new PErc20Delegator(
            NVDA,
            PeridottrollerInterface(CONTROLLER),
            InterestRateModel(IRM),
            STOCK_RATE,
            "Peridot NVDA Uniswap LP",
            "upNVDA",
            8,
            payable(LEDGER),
            DELEGATE,
            ""
        );
        PErc20Delegator upUsdg = new PErc20Delegator(
            USDG,
            PeridottrollerInterface(CONTROLLER),
            InterestRateModel(IRM),
            DOLLAR_RATE,
            "Peridot USDG Uniswap LP",
            "upUSDG",
            8,
            payable(LEDGER),
            DELEGATE,
            ""
        );
        address[] memory ms = new address[](5);
        ms[0] = PNVDA;
        ms[1] = PUSDG;
        ms[2] = PSPY;
        ms[3] = address(upNvda);
        ms[4] = address(upUsdg);
        RobinhoodLendingPriceAdapterN adapter = new RobinhoodLendingPriceAdapterN(LENDING_SOURCE, ms);
        vm.stopBroadcast();

        string memory j = "j";
        vm.serializeUint(j, "chainId", block.chainid);
        vm.serializeAddress(j, "deployer", msg.sender);
        vm.serializeUint(j, "block", block.number);
        vm.serializeAddress(j, "upNvda", address(upNvda));
        vm.serializeAddress(j, "upUsdg", address(upUsdg));
        vm.serializeBytes32(j, "adapterNRuntimeHash", address(adapter).codehash);
        string memory out = vm.serializeAddress(j, "adapterN", address(adapter));
        vm.writeJson(out, path);
        console2.log("upNVDA", address(upNvda));
        console2.log("upUSDG", address(upUsdg));
        console2.log("adapterN", address(adapter));
        _verify(path);
    }

    function _path() internal view returns (string memory) {
        return string.concat(
            vm.projectRoot(),
            "/remediation/nvda-optin/journals/",
            vm.envOr("NVDA_OPTIN_JOURNAL", string("nvda-optin-mainnet")),
            ".json"
        );
    }

    function _verify(string memory path) internal view {
        string memory raw = vm.readFile(path);
        address upNvda = vm.parseJsonAddress(raw, ".upNvda");
        address upUsdg = vm.parseJsonAddress(raw, ".upUsdg");
        address adapterAddr = vm.parseJsonAddress(raw, ".adapterN");
        _checkMarket(upNvda, NVDA, "upNVDA", STOCK_RATE);
        _checkMarket(upUsdg, USDG, "upUSDG", DOLLAR_RATE);

        RobinhoodLendingPriceAdapterN adapter = RobinhoodLendingPriceAdapterN(adapterAddr);
        require(adapterAddr.codehash == vm.parseJsonBytes32(raw, ".adapterNRuntimeHash"), "ADAPTER_CODE");
        require(address(adapter.source()) == LENDING_SOURCE, "ADAPTER_SOURCE");
        address[] memory ms = adapter.markets();
        require(ms.length == 5, "ADAPTER_MARKET_COUNT");
        require(
            ms[0] == PNVDA && ms[1] == PUSDG && ms[2] == PSPY && ms[3] == upNvda && ms[4] == upUsdg,
            "ADAPTER_MARKETS"
        );
        // Same arithmetic as the installed controller oracle for the markets it already prices.
        address installed = IController(CONTROLLER).oracle();
        require(
            adapter.getUnderlyingPrice(PNVDA) == IOracle(installed).getUnderlyingPrice(PNVDA),
            "PNVDA_PRICE"
        );
        require(
            adapter.getUnderlyingPrice(PUSDG) == IOracle(installed).getUnderlyingPrice(PUSDG),
            "PUSDG_PRICE"
        );
        try IOracle(installed).getUnderlyingPrice(PSPY) returns (uint256 p) {
            require(adapter.getUnderlyingPrice(PSPY) == p, "PSPY_PRICE");
        } catch {
            console2.log("installed oracle does not price pSPY yet (SPY margin t0 step 5 not run)");
        }
        require(adapter.getUnderlyingPrice(upNvda) == adapter.getUnderlyingPrice(PNVDA), "UPNVDA_PRICE");
        require(adapter.getUnderlyingPrice(upUsdg) == adapter.getUnderlyingPrice(PUSDG), "UPUSDG_PRICE");
        console2.log("NVDA_OPTIN_VERIFY_OK");
    }

    function _checkMarket(address m, address underlying_, string memory sym, uint256 rate) internal view {
        IOptInMarket market = IOptInMarket(m);
        require(m.code.length > 0, "NO_MARKET_CODE");
        require(market.admin() == LEDGER, "MARKET_ADMIN");
        require(market.implementation() == DELEGATE, "MARKET_IMPL");
        require(market.underlying() == underlying_, "MARKET_UNDERLYING");
        require(market.peridottroller() == CONTROLLER, "MARKET_CONTROLLER");
        require(market.interestRateModel() == IRM, "MARKET_IRM");
        require(market.decimals() == 8, "MARKET_DECIMALS");
        require(keccak256(bytes(market.symbol())) == keccak256(bytes(sym)), "MARKET_SYMBOL");
        require(market.actionDelay() == 3600, "MARKET_ACTION_DELAY");
        if (market.totalSupply() == 0) {
            require(market.exchangeRateStored() == rate, "MARKET_RATE");
        }
        require(market.totalBorrows() == 0, "MARKET_BORROWS");
    }
}

interface IController {
    function oracle() external view returns (address);
}

interface IOracle {
    function getUnderlyingPrice(address) external view returns (uint256);
}
