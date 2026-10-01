"""Keccak-256 (pure Python) and the few ABI pieces the close needs.

Keccak is the original Keccak submission used by Ethereum, not NIST SHA3-256 (different
padding). Tested against the ERC20 Transfer topic and known function selectors.
"""

from __future__ import annotations

from dataclasses import dataclass

_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]  # fmt: skip
_ROT = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]
_MASK = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    return ((x << n) | (x >> (64 - n))) & _MASK if n else x


def _f(a: list[list[int]]) -> None:
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        a[0][0] ^= rc


def keccak256(data: bytes) -> bytes:
    rate = 136
    msg = bytearray(data) + b"\x01"
    msg += b"\x00" * (-len(msg) % rate)
    msg[-1] |= 0x80
    a = [[0] * 5 for _ in range(5)]
    for off in range(0, len(msg), rate):
        block = msg[off : off + rate]
        for i in range(rate // 8):
            a[i % 5][i // 5] ^= int.from_bytes(block[8 * i : 8 * i + 8], "little")
        _f(a)
    out = b"".join(a[i % 5][i // 5].to_bytes(8, "little") for i in range(4))
    return out


def topic(signature: str) -> str:
    return "0x" + keccak256(signature.encode()).hex()


def selector(signature: str) -> str:
    return topic(signature)[:10]


def words(data: str) -> list[int]:
    raw = data[2:] if data.startswith("0x") else data
    return [int(raw[i : i + 64], 16) for i in range(0, len(raw), 64)]


def addr(word: int | str) -> str:
    if isinstance(word, str):
        word = int(word, 16)
    return "0x" + format(word, "064x")[-40:]


# Events from the verified sources (BridgeDeposit.sol and LimitedMinter.sol, see
# config/contracts.yaml for where each one was read).
BRIDGE_DEPOSIT_INITIATED = topic(
    "BridgeDepositInitiated(uint256,address,address,uint256,uint256,uint256,address,bytes32)"
)
BRIDGE_MINT_FULFILLED = topic(
    "BridgeMintFulfilled(address,address,uint256,uint256,bytes32,uint256)"
)
LIMITED_MINTER_MINTED = topic("Minted(address,address,address,uint256)")


@dataclass(frozen=True)
class BridgeOut:
    """BridgeDepositInitiated: tokens burned here, to be minted on dest_chain_id."""

    emitter: str
    deposit_id: int
    token: str
    sender: str
    amount: int
    fee: int
    dest_chain_id: int
    dest_recipient: str
    client_deposit_id: str


@dataclass(frozen=True)
class BridgeIn:
    """BridgeMintFulfilled: tokens minted here for a deposit on source_chain_id."""

    emitter: str
    token: str
    to: str
    amount: int
    source_chain_id: int
    source_tx_hash: str
    source_deposit_id: int


@dataclass(frozen=True)
class PrimaryMint:
    """LimitedMinter Minted: tokens minted by a MINTER_ROLE account to the destination."""

    emitter: str
    token: str
    minter: str
    destination: str
    amount: int


def decode(log: dict) -> BridgeOut | BridgeIn | PrimaryMint | None:
    t = [x.lower() for x in log["topics"]]
    if not t:
        return None
    emitter = log["address"].lower()
    w = words(log["data"])
    if t[0] == BRIDGE_DEPOSIT_INITIATED and len(t) == 4 and len(w) == 5:
        return BridgeOut(
            emitter,
            int(t[1], 16),
            addr(t[2]),
            addr(t[3]),
            w[0],
            w[1],
            w[2],
            addr(w[3]),
            "0x" + format(w[4], "064x"),
        )
    if t[0] == BRIDGE_MINT_FULFILLED and len(t) == 4 and len(w) == 3:
        return BridgeIn(
            emitter, addr(t[1]), addr(t[2]), w[0], w[1], "0x" + format(w[2], "064x"), int(t[3], 16)
        )
    if t[0] == LIMITED_MINTER_MINTED and len(t) == 4 and len(w) == 1:
        return PrimaryMint(emitter, addr(t[1]), addr(t[2]), addr(t[3]), w[0])
    return None
