# Mainnet deployment addresses

Robinhood Chain, chain ID **4663**. These are the archived canary addresses, verified against the new runtime report where applicable. Paused borrowing and zero current LP liquidity must be disclosed separately from deployment.

| Component | Explorer |
| --- | --- |
| existingAddresses/usd | [`0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168`](https://robinhoodchain.blockscout.com/address/0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168) |
| existingAddresses/stock | [`0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC`](https://robinhoodchain.blockscout.com/address/0xd0601CE157Db5bdC3162BbaC2a2C8aF5320D9EEC) |
| existingAddresses/pUsd | [`0x55aed0569c8f0d166d71face57b57c2f2624a563`](https://robinhoodchain.blockscout.com/address/0x55aed0569c8f0d166d71face57b57c2f2624a563) |
| existingAddresses/pStock | [`0xa155cccb986774ae818b3f10f07d01d1b7a47b26`](https://robinhoodchain.blockscout.com/address/0xa155cccb986774ae818b3f10f07d01d1b7a47b26) |
| existingAddresses/controller | [`0x6148183676e304dbe63a85c350c208da3ceac39c`](https://robinhoodchain.blockscout.com/address/0x6148183676e304dbe63a85c350c208da3ceac39c) |
| existingAddresses/feed | [`0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15`](https://robinhoodchain.blockscout.com/address/0x379EC4f7C378F34a1B47E4F3cbeBCbAC3E8E9F15) |
| existingAddresses/guard | [`0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741`](https://robinhoodchain.blockscout.com/address/0xaE4D4DdB8dD646951d54fE9B13BE23DcB61C6741) |
| marginAddresses/executor | [`0x6A45Ae86bD992d250580d08D340A06A04D478977`](https://robinhoodchain.blockscout.com/address/0x6A45Ae86bD992d250580d08D340A06A04D478977) |
| marginAddresses/marginVault | [`0x04D4A5555b7a37017A67B4D21A1Da5838de28B9e`](https://robinhoodchain.blockscout.com/address/0x04D4A5555b7a37017A67B4D21A1Da5838de28B9e) |
| marginAddresses/config | [`0x09F94fe0B79E000c8a26617c63E3427fdECB528b`](https://robinhoodchain.blockscout.com/address/0x09F94fe0B79E000c8a26617c63E3427fdECB528b) |
| marginAddresses/riskEngine | [`0xC8b178C3c74570472FF1eeE0DD559e61AF9f9678`](https://robinhoodchain.blockscout.com/address/0xC8b178C3c74570472FF1eeE0DD559e61AF9f9678) |
| marginAddresses/quoter | [`0xeD3c353Ab237329BD53CC7eB24E66B370155FE6e`](https://robinhoodchain.blockscout.com/address/0xeD3c353Ab237329BD53CC7eB24E66B370155FE6e) |
| marginAddresses/liquidator | [`0x1434CDa56d0Aeac4d5abC16F91ca76a8A989083c`](https://robinhoodchain.blockscout.com/address/0x1434CDa56d0Aeac4d5abC16F91ca76a8A989083c) |
| marginAddresses/oracle | [`0x63150Eb3DDf71420dA0b09b66838aab398f7dEdD`](https://robinhoodchain.blockscout.com/address/0x63150Eb3DDf71420dA0b09b66838aab398f7dEdD) |
| marginAddresses/guardedSource | [`0x25E02b142E0785a59D85a6CA820c400e601a9E8E`](https://robinhoodchain.blockscout.com/address/0x25E02b142E0785a59D85a6CA820c400e601a9E8E) |
| marginAddresses/flashVault | [`0x79d33c9BbC1D0711e88C5602f86135Ab4C088b06`](https://robinhoodchain.blockscout.com/address/0x79d33c9BbC1D0711e88C5602f86135Ab4C088b06) |
| marginAddresses/router | [`0xa32C34F100B4F1f36ECA09c427a098f99F4423F0`](https://robinhoodchain.blockscout.com/address/0xa32C34F100B4F1f36ECA09c427a098f99F4423F0) |
| marginAddresses/swapModule | [`0xa2a022B17e0201894937755584EecC878AEFe1bf`](https://robinhoodchain.blockscout.com/address/0xa2a022B17e0201894937755584EecC878AEFe1bf) |
| marginAddresses/accountFactory | [`0x88BDf12F3b6B5C11bd0Ed5c117181FeA3130D15C`](https://robinhoodchain.blockscout.com/address/0x88BDf12F3b6B5C11bd0Ed5c117181FeA3130D15C) |
| marginAddresses/insuranceFund | [`0x17c72B8f171999C4d8863a3517D1C5d9cBfBb068`](https://robinhoodchain.blockscout.com/address/0x17c72B8f171999C4d8863a3517D1C5d9cBfBb068) |
| marginAddresses/feeDistributor | [`0x8D2707946A9d7abce3d8fb3FafDc40f4811180b7`](https://robinhoodchain.blockscout.com/address/0x8D2707946A9d7abce3d8fb3FafDc40f4811180b7) |

The installed lending adapter is [`0xe4e03c2fdaef915ace705d106b2660b1e342a2e4`](https://robinhoodchain.blockscout.com/address/0xe4e03c2fdaef915ace705d106b2660b1e342a2e4). Runtime, immutable wiring, price units and controller pointer were verified at block 73,329,306 in `evidence/installed-adapter.json`. The original StockSimplePriceOracle remains the USD18 backing source; its address is not the controller’s current oracle pointer.

The vault proxy remains `0x280825b2d856706Ff7E0d6351CcB2e935E1a9A2f`; its current implementation is [`0x17f0cf262fbbf27e44756dba6d852815695e9c4a`](https://robinhoodchain.blockscout.com/address/0x17f0cf262fbbf27e44756dba6d852815695e9c4a), independently verified at block 73,403,815. The archived V1 implementation is no longer active. See [execution evidence](evidence/vault-upgrade-execution.json).

The frozen address manifest remains unchanged until a separately versioned integration update is prepared.
