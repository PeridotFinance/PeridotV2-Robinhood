// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { IRamsesNfp } from "../../src/interfaces/IRamsesV3.sol";
import { PoolDouble, GaugeDouble } from "./RamsesV3Double.sol";

contract MultiFactoryDouble {
    mapping(bytes32 => address) private pools;
    mapping(address => address) public gaugeForPool;

    function set(PoolDouble pool, address gauge) external {
        pools[keccak256(abi.encode(pool.token0(), pool.token1(), pool.tickSpacing()))] =
            address(pool);
        gaugeForPool[address(pool)] = gauge;
    }

    function getPool(address a, address b, int24 spacing) external view returns (address) {
        return pools[keccak256(abi.encode(a, b, spacing))];
    }

    function voter() external view returns (address) {
        return address(this);
    }

    function RamsesV3Factory() external view returns (address) {
        return address(this);
    }
}

contract MultiNfpDouble is IRamsesNfp {
    mapping(bytes32 => PoolDouble) private pools;
    mapping(uint256 => PoolDouble) private positionPools;
    mapping(address => GaugeDouble) private gauges;
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

    constructor(address f) {
        factory = f;
        voter = f;
    }

    function deployer() external view returns (address) {
        return factory;
    }

    function addPool(PoolDouble p, GaugeDouble g) external {
        pools[keccak256(abi.encode(p.token0(), p.token1()))] = p;
        gauges[address(p)] = g;
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

    function balanceOf(address owner) external view returns (uint256 count) {
        for (uint256 id = 1; id < nextId; id++) {
            if (ownerOf[id] == owner) ++count;
        }
    }

    function positions(uint256 id) external view returns (Position memory) {
        require(ownerOf[id] != address(0));
        return positionPools[id].position(id);
    }

    function mint(MintParams calldata p)
        external
        payable
        returns (uint256 id, uint128 l, uint256 a, uint256 b)
    {
        PoolDouble pool = pools[keccak256(abi.encode(p.token0, p.token1))];
        require(
            p.deadline >= block.timestamp && p.token0 == pool.token0() && p.token1 == pool.token1()
                && p.tickSpacing == 10
        );
        id = nextId++;
        positionPools[id] = pool;
        ownerOf[id] = p.recipient;
        (l, a, b) = pool.add(id, p.tickLower, p.tickUpper, p.amount0Desired, p.amount1Desired);
        _pay(pool, a, b, p.amount0Min, p.amount1Min);
        if (lieAdd) ++a;
    }

    function increaseLiquidity(IncreaseLiquidityParams calldata p)
        external
        payable
        returns (uint128 l, uint256 a, uint256 b)
    {
        PoolDouble pool = positionPools[p.tokenId];
        require(p.deadline >= block.timestamp);
        Position memory pos = pool.position(p.tokenId);
        (l, a, b) =
            pool.add(p.tokenId, pos.tickLower, pos.tickUpper, p.amount0Desired, p.amount1Desired);
        _pay(pool, a, b, p.amount0Min, p.amount1Min);
        if (lieAdd) ++a;
    }

    function _pay(PoolDouble pool, uint256 a, uint256 b, uint256 m0, uint256 m1) private {
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
        PoolDouble pool = positionPools[p.tokenId];
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
        PoolDouble pool = positionPools[p.tokenId];
        (a, b) = pool.collect(p.tokenId, p.recipient, p.amount0Max, p.amount1Max);
        if (lieCollect) ++a;
    }

    function getReward(uint256 id, address[] calldata tokens) external auth(id) {
        lastHarvest = ++sequence;
        gauges[address(positionPools[id])].getRewardForOwner(id, tokens);
    }

    function burn(uint256 id) external payable auth(id) {
        lastBurn = ++sequence;
        Position memory p = positionPools[id].position(id);
        if (p.liquidity != 0 || p.tokensOwed0 != 0 || p.tokensOwed1 != 0) revert NotEmpty();
        if (!skipBurn) delete ownerOf[id];
    }
}
