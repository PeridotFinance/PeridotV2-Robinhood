// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

/// @notice Ramses CL uses tickSpacing (int24), not Uniswap v3's fee (uint24).
interface IRamsesPool {
    function token0() external view returns (address);
    function token1() external view returns (address);
    function factory() external view returns (address);
    function tickSpacing() external view returns (int24);
    function fee() external view returns (uint24);
    function liquidity() external view returns (uint128);
    function slot0() external view returns (uint160, int24, uint16, uint16, uint16, uint24, bool);
    function feeGrowthGlobal0X128() external view returns (uint256);
    function feeGrowthGlobal1X128() external view returns (uint256);
    function protocolFees() external view returns (uint128, uint128);
    function swap(address, bool, int256, uint160, bytes calldata) external returns (int256, int256);
    function _advancePeriod() external;
}

interface IRamsesNfp {
    struct Position {
        address token0;
        address token1;
        int24 tickSpacing;
        int24 tickLower;
        int24 tickUpper;
        uint128 liquidity;
        uint256 feeGrowthInside0LastX128;
        uint256 feeGrowthInside1LastX128;
        uint128 tokensOwed0;
        uint128 tokensOwed1;
    }

    struct MintParams {
        address token0;
        address token1;
        int24 tickSpacing;
        int24 tickLower;
        int24 tickUpper;
        uint256 amount0Desired;
        uint256 amount1Desired;
        uint256 amount0Min;
        uint256 amount1Min;
        address recipient;
        uint256 deadline;
    }

    struct IncreaseLiquidityParams {
        uint256 tokenId;
        uint256 amount0Desired;
        uint256 amount1Desired;
        uint256 amount0Min;
        uint256 amount1Min;
        uint256 deadline;
    }

    struct DecreaseLiquidityParams {
        uint256 tokenId;
        uint128 liquidity;
        uint256 amount0Min;
        uint256 amount1Min;
        uint256 deadline;
    }

    struct CollectParams {
        uint256 tokenId;
        address recipient;
        uint128 amount0Max;
        uint128 amount1Max;
    }
    function positions(uint256) external view returns (Position memory);
    function balanceOf(address) external view returns (uint256);
    function ownerOf(uint256) external view returns (address);
    function mint(MintParams calldata) external payable returns (uint256, uint128, uint256, uint256);
    function increaseLiquidity(IncreaseLiquidityParams calldata)
        external
        payable
        returns (uint128, uint256, uint256);
    function decreaseLiquidity(DecreaseLiquidityParams calldata)
        external
        payable
        returns (uint256, uint256);
    function collect(CollectParams calldata) external payable returns (uint256, uint256);
    function burn(uint256) external payable;
    function getReward(uint256, address[] calldata) external;
    function deployer() external view returns (address);
}

interface IRamsesGauge {
    function voter() external view returns (address);
    function pool() external view returns (address);
    function nfpManager() external view returns (address);
    function getRewardTokens() external view returns (address[] memory);
    function getReward(uint256, address[] calldata) external;
    function getRewardForOwner(uint256, address[] calldata) external;
    function earned(address, uint256) external view returns (uint256);
    function rewardRate(address) external view returns (uint256);
    function periodEarned(uint256, address, uint256) external view returns (uint256);
    function isAuthorizedClaimer(address) external view returns (bool);
    function rewardValidator() external view returns (address);
}

interface IRamsesFactory {
    function voter() external view returns (address);
    function getPool(address, address, int24) external view returns (address);
}

interface IRamsesVoter {
    function gaugeForPool(address) external view returns (address);
}

interface IRamsesDeployer {
    function RamsesV3Factory() external view returns (address);
}
