// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { ERC20 } from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { LiquidityAmounts } from "@uniswap/v4-periphery/src/libraries/LiquidityAmounts.sol";
import { SqrtPriceMath } from "@uniswap/v4-core/src/libraries/SqrtPriceMath.sol";
import { IRamsesNfp } from "../../src/interfaces/IRamsesV3.sol";
import { IRamsesReferenceOracle } from "../../src/RamsesV3PairedAdapter.sol";

contract TokenDouble is ERC20 {
    uint8 private immutable _decimals;
    bool public taxed;
    address public taxSender;

    constructor(string memory name_, uint8 decimals_) ERC20(name_, name_) {
        _decimals = decimals_;
    }

    function decimals() public view override returns (uint8) {
        return _decimals;
    }

    function mint(address to, uint256 n) external {
        _mint(to, n);
    }

    function setTax(bool b, address sender) external {
        taxed = b;
        taxSender = sender;
    }

    function _update(address from, address to, uint256 n) internal override {
        if (taxed && from == taxSender && n > 0) {
            super._update(from, address(0), 1);
            --n;
        }
        super._update(from, to, n);
    }
}

contract OracleDouble is IRamsesReferenceOracle {
    uint160 public price;
    uint256 public updatedAt;
    bool public fail;
    error StaleOracle();

    function set(uint160 p) external {
        price = p;
        updatedAt = block.timestamp;
        fail = false;
    }

    function setFail(bool b) external {
        fail = b;
    }

    function referenceSqrtPriceX96(bytes32, bool) external view returns (uint160) {
        if (fail || block.timestamp > updatedAt + 12 hours) revert StaleOracle();
        return price;
    }
}

contract FactoryVoterDouble {
    address public pool;
    address public gauge;

    function set(address p, address g) external {
        pool = p;
        gauge = g;
    }

    function getPool(address, address, int24) external view returns (address) {
        return pool;
    }

    function voter() external view returns (address) {
        return address(this);
    }

    function RamsesV3Factory() external view returns (address) {
        return address(this);
    }

    function gaugeForPool(address) external view returns (address) {
        return gauge;
    }
}

/// @dev Behavioral model of the exercised CL surface, not a replacement for Ramses core.
/// Uses actual rounded v3 amount math, fee growth, time weighted L/activeL and Thursday periods.
/// It does not implement the swap tick-crossing engine: setTick checkpoints old/new active sets.
contract PoolDouble {
    address public token0;
    address public token1;
    address public factory;
    address public manager;
    int24 public constant tickSpacing = 10;
    uint24 public constant fee = 100;
    uint24 public constant protocolShare = 500000;
    uint160 public sqrtPrice;
    int24 public tick;
    uint128 public baseLiquidity = 742421193053890387;
    uint256 private lastTime;
    uint256[] private ids;
    mapping(uint256 => IRamsesNfp.Position) private _positions;
    mapping(uint256 => mapping(uint256 => uint256)) public secondsX96;
    uint256 public protocol0;
    uint256 public protocol1;
    error NotManager();
    modifier onlyManager() {
        if (msg.sender != manager) revert NotManager();
        _;
    }

    constructor(address a, address b, address f, int24 t) {
        token0 = a;
        token1 = b;
        factory = f;
        setTick(t);
    }

    function setManager(address m) external {
        require(manager == address(0));
        manager = m;
    }

    function slot0() external view returns (uint160, int24, uint16, uint16, uint16, uint24, bool) {
        return (sqrtPrice, tick, 0, 1000, 1000, protocolShare, true);
    }

    function liquidity() public view returns (uint128 l) {
        l = baseLiquidity;
        for (uint256 i; i < ids.length; i++) {
            IRamsesNfp.Position memory p = _positions[ids[i]];
            if (tick >= p.tickLower && tick < p.tickUpper) l += p.liquidity;
        }
    }

    function setTick(int24 t) public {
        _advancePeriod();
        tick = t;
        sqrtPrice = TickMath.getSqrtPriceAtTick(t);
    }

    function _advancePeriod() public {
        if (lastTime == 0) {
            lastTime = block.timestamp;
            return;
        }
        uint256 from = lastTime;
        uint128 total = liquidity();
        while (from < block.timestamp) {
            uint256 period = from / 1 weeks;
            uint256 end = Math.min(block.timestamp, (period + 1) * 1 weeks);
            for (uint256 i; i < ids.length; i++) {
                IRamsesNfp.Position memory p = _positions[ids[i]];
                if (tick >= p.tickLower && tick < p.tickUpper) {
                    secondsX96[
                        period
                    ][ids[i]] += Math.mulDiv((end - from) << 96, p.liquidity, total);
                }
            }
            from = end;
        }
        lastTime = block.timestamp;
    }

    function position(uint256 id) external view returns (IRamsesNfp.Position memory) {
        return _positions[id];
    }

    function amounts(int24 lo, int24 hi, uint128 l, bool roundUp)
        public
        view
        returns (uint256 a, uint256 b)
    {
        uint160 low = TickMath.getSqrtPriceAtTick(lo);
        uint160 high = TickMath.getSqrtPriceAtTick(hi);
        if (sqrtPrice <= low) {
            a = SqrtPriceMath.getAmount0Delta(low, high, l, roundUp);
        } else if (sqrtPrice >= high) {
            b = SqrtPriceMath.getAmount1Delta(low, high, l, roundUp);
        } else {
            a = SqrtPriceMath.getAmount0Delta(sqrtPrice, high, l, roundUp);
            b = SqrtPriceMath.getAmount1Delta(low, sqrtPrice, l, roundUp);
        }
    }

    function add(uint256 id, int24 lo, int24 hi, uint256 d0, uint256 d1)
        external
        onlyManager
        returns (uint128 l, uint256 a, uint256 b)
    {
        _advancePeriod();
        IRamsesNfp.Position storage p = _positions[id];
        if (p.token0 == address(0)) {
            ids.push(id);
            p.token0 = token0;
            p.token1 = token1;
            p.tickSpacing = 10;
            p.tickLower = lo;
            p.tickUpper = hi;
        }
        l = LiquidityAmounts.getLiquidityForAmounts(
            sqrtPrice, TickMath.getSqrtPriceAtTick(lo), TickMath.getSqrtPriceAtTick(hi), d0, d1
        );
        (a, b) = amounts(lo, hi, l, true);
        p.liquidity += l;
    }

    function remove(uint256 id, uint128 l) external onlyManager returns (uint256 a, uint256 b) {
        _advancePeriod();
        IRamsesNfp.Position storage p = _positions[id];
        (a, b) = amounts(p.tickLower, p.tickUpper, l, false);
        p.liquidity -= l;
        p.tokensOwed0 += uint128(a);
        p.tokensOwed1 += uint128(b);
    }

    function collect(uint256 id, address to, uint128 max0, uint128 max1)
        external
        onlyManager
        returns (uint256 a, uint256 b)
    {
        IRamsesNfp.Position storage p = _positions[id];
        a = Math.min(max0, p.tokensOwed0);
        b = Math.min(max1, p.tokensOwed1);
        p.tokensOwed0 -= uint128(a);
        p.tokensOwed1 -= uint128(b);
        if (a != 0) IERC20(token0).transfer(to, a);
        if (b != 0) IERC20(token1).transfer(to, b);
    }

    /// @dev Fee input must be funded in the pool first, like a swap/flash callback.
    function accrueFees(uint256 gross0, uint256 gross1) external {
        _advancePeriod();
        uint256 lp0 = gross0 - gross0 * protocolShare / 1e6;
        uint256 lp1 = gross1 - gross1 * protocolShare / 1e6;
        protocol0 += gross0 - lp0;
        protocol1 += gross1 - lp1;
        uint128 total = liquidity();
        for (uint256 i; i < ids.length; i++) {
            IRamsesNfp.Position storage p = _positions[ids[i]];
            if (tick >= p.tickLower && tick < p.tickUpper) {
                uint256 growth0 = Math.mulDiv(lp0, 1 << 128, total);
                uint256 growth1 = Math.mulDiv(lp1, 1 << 128, total);
                p.feeGrowthInside0LastX128 += growth0;
                p.feeGrowthInside1LastX128 += growth1;
                p.tokensOwed0 += uint128(Math.mulDiv(growth0, p.liquidity, 1 << 128));
                p.tokensOwed1 += uint128(Math.mulDiv(growth1, p.liquidity, 1 << 128));
            }
        }
    }
}

contract NfpDouble is IRamsesNfp {
    PoolDouble public pool;
    GaugeDouble public gauge;
    address public immutable factory;
    address public immutable voter;
    uint256 public nextId = 1;
    mapping(uint256 => address) public ownerOf;
    bool public lieCollect;
    bool public lieAdd;
    bool public skipBurn;
    uint256 public lastHarvest;
    uint256 public lastDecrease;
    uint256 public lastBurn;
    uint256 private sequence;
    error Unauthorized();
    error Slippage();
    error NotEmpty();

    constructor(PoolDouble p, address f) {
        pool = p;
        factory = f;
        voter = f;
    }

    function deployer() external view returns (address) {
        return factory;
    }

    function setGauge(GaugeDouble g) external {
        gauge = g;
    }

    function setFaults(bool collect_, bool add_, bool burn_) external {
        lieCollect = collect_;
        lieAdd = add_;
        skipBurn = burn_;
    }
    modifier auth(uint256 id) {
        if (ownerOf[id] != msg.sender) revert Unauthorized();
        _;
    }

    function positions(uint256 id) external view returns (Position memory) {
        require(ownerOf[id] != address(0));
        return pool.position(id);
    }

    function mint(MintParams calldata p)
        external
        payable
        returns (uint256 id, uint128 l, uint256 a, uint256 b)
    {
        require(
            p.deadline >= block.timestamp && p.token0 == pool.token0() && p.token1 == pool.token1()
                && p.tickSpacing == 10
        );
        id = nextId++;
        ownerOf[id] = p.recipient;
        (l, a, b) = pool.add(id, p.tickLower, p.tickUpper, p.amount0Desired, p.amount1Desired);
        _pay(a, b, p.amount0Min, p.amount1Min);
        if (lieAdd) ++a;
    }

    function increaseLiquidity(IncreaseLiquidityParams calldata p)
        external
        payable
        returns (uint128 l, uint256 a, uint256 b)
    {
        require(p.deadline >= block.timestamp);
        Position memory pos = pool.position(p.tokenId);
        (l, a, b) = pool.add(
            p.tokenId, pos.tickLower, pos.tickUpper, p.amount0Desired, p.amount1Desired
        );
        _pay(a, b, p.amount0Min, p.amount1Min);
        if (lieAdd) ++a;
    }

    function _pay(uint256 a, uint256 b, uint256 m0, uint256 m1) private {
        if (a < m0 || b < m1) revert Slippage();
        if (a != 0) IERC20(pool.token0()).transferFrom(msg.sender, address(pool), a);
        if (b != 0) IERC20(pool.token1()).transferFrom(msg.sender, address(pool), b);
    }

    function decreaseLiquidity(DecreaseLiquidityParams calldata p)
        external
        payable
        auth(p.tokenId)
        returns (uint256 a, uint256 b)
    {
        require(p.deadline >= block.timestamp && p.liquidity > 0);
        lastDecrease = ++sequence;
        (a, b) = pool.remove(p.tokenId, p.liquidity);
        if (a < p.amount0Min || b < p.amount1Min) revert Slippage();
    }

    function collect(CollectParams calldata p)
        external
        payable
        auth(p.tokenId)
        returns (uint256 a, uint256 b)
    {
        (a, b) = pool.collect(p.tokenId, p.recipient, p.amount0Max, p.amount1Max);
        if (lieCollect) ++a;
    }

    function getReward(uint256 id, address[] calldata tokens) external auth(id) {
        lastHarvest = ++sequence;
        gauge.getRewardForOwner(id, tokens);
    }

    function burn(uint256 id) external payable auth(id) {
        lastBurn = ++sequence;
        Position memory p = pool.position(id);
        if (p.liquidity != 0 || p.tokensOwed0 != 0 || p.tokensOwed1 != 0) revert NotEmpty();
        if (!skipBurn) delete ownerOf[id];
    }
}

contract GaugeDouble {
    PoolDouble public pool;
    NfpDouble public nfpManager;
    address[] private tokens;
    uint256 public immutable firstPeriod;
    mapping(uint256 => mapping(address => uint256)) public emissions;
    mapping(uint256 => mapping(uint256 => mapping(address => uint256))) public claimed;
    bool public slash;
    error Unauthorized();

    constructor(PoolDouble p, NfpDouble n, address[] memory ts) {
        pool = p;
        nfpManager = n;
        tokens = ts;
        firstPeriod = block.timestamp / 1 weeks;
    }

    function voter() external view returns (address) {
        return nfpManager.voter();
    }

    function getRewardTokens() external view returns (address[] memory) {
        return tokens;
    }

    function setTokens(address[] memory ts) external {
        tokens = ts;
    }

    function fund(address t, uint256 period, uint256 n) external {
        TokenDouble(t).mint(address(this), n);
        emissions[period][t] += n;
    }

    function setSlash(bool b) external {
        slash = b;
    }

    function rewardRate(address t) external view returns (uint256) {
        return emissions[block.timestamp / 1 weeks][t] / 1 weeks;
    }

    function earned(address t, uint256 id) public view returns (uint256 total) {
        for (uint256 p = firstPeriod; p <= block.timestamp / 1 weeks; p++) {
            total += periodEarned(p, t, id);
        }
    }

    function periodEarned(uint256 p, address t, uint256 id) public view returns (uint256) {
        uint256 total = Math.mulDiv(emissions[p][t], pool.secondsX96(p, id), uint256(1 weeks) << 96);
        return total - claimed[p][id][t];
    }

    function getReward(uint256, address[] calldata) external pure {
        revert Unauthorized();
    }

    function getRewardForOwner(uint256 id, address[] calldata ts) external {
        if (msg.sender != address(nfpManager)) revert Unauthorized();
        pool._advancePeriod();
        for (uint256 i; i < ts.length; i++) {
            for (uint256 p = firstPeriod; p <= block.timestamp / 1 weeks; p++) {
                uint256 n = periodEarned(p, ts[i], id);
                claimed[p][id][ts[i]] += n;
                if (n != 0) IERC20(ts[i])
                    .transfer(slash ? address(0xdead) : nfpManager.ownerOf(id), n);
            }
        }
    }
}
