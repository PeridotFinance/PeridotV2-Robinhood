// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { TokenDouble, PoolDouble } from "./RamsesV3Double.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";

interface ISwapCallback {
    function uniswapV3SwapCallback(int256, int256, bytes calldata) external;
}

contract PausableTokenDouble is TokenDouble {
    bool public paused;
    bool public oraclePaused;
    constructor(string memory n, uint8 d) TokenDouble(n, d) { }

    function setOraclePaused(bool b) external {
        oraclePaused = b;
    }

    function setPaused(bool b) external {
        paused = b;
    }
}

contract FeedDouble {
    uint8 public immutable decimals;
    int256 public answer;
    uint256 public updatedAt;
    uint80 public round = 1;
    uint80 public answered = 1;
    bool public fail;

    constructor(uint8 d, int256 a) {
        decimals = d;
        set(a);
    }

    function set(int256 a) public {
        answer = a;
        updatedAt = block.timestamp;
    }

    function setTimestamp(uint256 t) external {
        updatedAt = t;
    }

    function setRound(uint80 r, uint80 a) external {
        round = r;
        answered = a;
    }

    function setFail(bool b) external {
        fail = b;
    }

    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80) {
        require(!fail, "FEED");
        return (round, answer, updatedAt, updatedAt, answered);
    }
}

/// @dev Adds fixed-slot0 swaps to the Phase 1 NFT/gauge double. Real tick-crossing execution is fork-tested.
contract SwapPoolDouble is PoolDouble {
    address public callbackRelay;

    function setCallbackRelay(address relay) external {
        callbackRelay = relay;
    }
    uint16 public outputBps = 9999;
    bool public lie;
    constructor(address a, address b, address f, int24 t) PoolDouble(a, b, f, t) { }

    function setSwapFault(uint16 bps, bool lie_) external {
        outputBps = bps;
        lie = lie_;
    }

    function swap(address to, bool zero, int256 amount, uint160, bytes calldata data)
        external
        returns (int256 a, int256 b)
    {
        require(amount > 0);
        uint256 ratio = Math.mulDiv(sqrtPrice, sqrtPrice, 1 << 64);
        uint256 out = zero
            ? Math.mulDiv(uint256(amount), ratio, 1 << 128)
            : Math.mulDiv(uint256(amount), 1 << 128, ratio);
        out = Math.mulDiv(out, outputBps, 10_000);
        a = zero ? amount : -int256(out);
        b = zero ? -int256(out) : amount;
        IERC20(zero ? token1 : token0).transfer(to, out);
        uint256 before_ = IERC20(zero ? token0 : token1).balanceOf(address(this));
        if (callbackRelay == address(0)) {
            ISwapCallback(msg.sender).uniswapV3SwapCallback(a, b, data);
        } else {
            CallbackRelay(callbackRelay).relay(msg.sender, a, b, data);
        }
        require(
            IERC20(zero ? token0 : token1).balanceOf(address(this)) == before_ + uint256(amount)
        );
        if (lie) ++a;
    }
}

contract CallbackRelay {
    function relay(address adapter, int256 a, int256 b, bytes calldata data) external {
        ISwapCallback(adapter).uniswapV3SwapCallback(a, b, data);
    }
}
