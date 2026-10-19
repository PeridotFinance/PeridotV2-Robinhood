// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;
import { IRamsesPool } from "../../src/interfaces/IRamsesV3.sol";
import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { TickMath } from "@uniswap/v4-core/src/libraries/TickMath.sol";
import { VaultMath } from "baseline/src/libraries/VaultMath.sol";
import { Math } from "@openzeppelin/contracts/utils/math/Math.sol";

/// @dev LOCAL ANVIL ONLY: subsequent feed updates are simulated after initial real-feed verification.
contract KeeperFeed {
    int256 public answer;
    uint256 public updatedAt;

    function decimals() external pure returns (uint8) {
        return 8;
    }

    function setPrice(int256 value) external {
        answer = value;
        updatedAt = block.timestamp;
    }

    function latestRoundData() external view returns (uint80, int256, uint256, uint256, uint80) {
        return (1, answer, updatedAt, updatedAt, 1);
    }
}

contract KeeperForkDriver {
    IRamsesPool constant pool = IRamsesPool(0xdac1904D823F7d6bcAaF431ceEe40c934fEd321b);
    KeeperFeed constant feed = KeeperFeed(0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15);

    function shift(int24 offset) external {
        (, int24 tick,,,,,) = pool.slot0();
        int24 target = tick + offset;
        if (offset != 0) {
            pool.swap(
                address(this), offset < 0, int256(1e25), TickMath.getSqrtPriceAtTick(target), ""
            );
        }
        (, int24 actual,,,,,) = pool.slot0();
        uint256 quote = VaultMath.quoteAtTick(actual, 1e18, pool.token1(), pool.token0());
        feed.setPrice(int256(quote * 100));
    }

    function align(uint256 price) external {
        uint256 ratio = Math.mulDiv(Math.mulDiv(1e18, 1e18, price), 1e18, 1e6);
        uint160 ref = uint160(Math.sqrt(Math.mulDiv(ratio, 1 << 128, 1e18)) << 32);
        (uint160 spot,,,,,,) = pool.slot0();
        if (spot != ref) pool.swap(address(this), spot > ref, int256(1e25), ref, "");
    }

    function uniswapV3SwapCallback(int256 a, int256 b, bytes calldata) external {
        require(msg.sender == address(pool));
        if (a > 0) IERC20(pool.token0()).transfer(msg.sender, uint256(a));
        if (b > 0) IERC20(pool.token1()).transfer(msg.sender, uint256(b));
    }
}
