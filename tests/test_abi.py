"""Keccak and event decoding, checked against known values."""

from cierre.abi import keccak256, selector, topic
from cierre.ledger import TRANSFER_TOPIC


def test_keccak_known_vectors():
    assert keccak256(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )
    assert topic("Transfer(address,address,uint256)") == TRANSFER_TOPIC


def test_selectors_seen_onchain():
    assert selector("mint(address,uint256)") == "0x40c10f19"
    assert selector("burn(uint256)") == "0x42966c68"
    assert selector("depositForBridge(address,uint256,uint256,address,bytes32)") == "0x0522eec2"
    assert (
        selector("fulfillBridgeMint(address,address,uint256,uint256,bytes32,uint256)")
        == "0x55c2730d"
    )
