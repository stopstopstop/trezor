import ustruct as struct
from micropython import const

from trezor.crypto.hashlib import blake2b
from trezor.messages import FailureType, InputScriptType
from trezor.messages.SignTx import SignTx
from trezor.messages.TransactionType import TransactionType
from trezor.messages.TxInputType import TxInputType
from trezor.utils import HashWriter, ensure

from apps.common.coininfo import CoinInfo
from apps.common.seed import Keychain
from apps.wallet.sign_tx.bitcoinlike import Bitcoinlike
from apps.wallet.sign_tx.common import SigningError
from apps.wallet.sign_tx.multisig import multisig_get_pubkeys
from apps.wallet.sign_tx.scripts import output_script_multisig, output_script_p2pkh
from apps.wallet.sign_tx.segwit_bip143 import Bip143
from apps.wallet.sign_tx.writers import (
    TX_HASH_SIZE,
    get_tx_hash,
    write_bytes_fixed,
    write_bytes_prefixed,
    write_bytes_reversed,
    write_uint32,
    write_uint64,
    write_varint,
)

if False:
    from typing import Union
    from apps.wallet.sign_tx.writers import Writer

OVERWINTERED = const(0x80000000)


def derive_script_code(txi: TxInputType, pubkeyhash: bytes) -> bytearray:

    if txi.multisig:
        return output_script_multisig(
            multisig_get_pubkeys(txi.multisig), txi.multisig.m
        )

    p2pkh = txi.script_type == InputScriptType.SPENDADDRESS
    if p2pkh:
        return output_script_p2pkh(pubkeyhash)

    else:
        raise SigningError(
            FailureType.DataError, "Unknown input script type for zip143 script code"
        )


class Zip143(Bip143):
    def __init__(self, branch_id: int) -> None:
        self.branch_id = branch_id
        self.h_prevouts = HashWriter(blake2b(outlen=32, personal=b"ZcashPrevoutHash"))
        self.h_sequence = HashWriter(blake2b(outlen=32, personal=b"ZcashSequencHash"))
        self.h_outputs = HashWriter(blake2b(outlen=32, personal=b"ZcashOutputsHash"))

    def get_prevouts_hash(self, coin: CoinInfo) -> bytes:
        return get_tx_hash(self.h_prevouts)

    def get_sequence_hash(self, coin: CoinInfo) -> bytes:
        return get_tx_hash(self.h_sequence)

    def get_outputs_hash(self, coin: CoinInfo) -> bytes:
        return get_tx_hash(self.h_outputs)

    def preimage_hash(
        self,
        coin: CoinInfo,
        tx: SignTx,
        txi: TxInputType,
        pubkeyhash: bytes,
        sighash: int,
    ) -> bytes:
        h_preimage = HashWriter(
            blake2b(
                outlen=32, personal=b"ZcashSigHash" + struct.pack("<I", self.branch_id)
            )
        )

        ensure(coin.overwintered)
        ensure(tx.version == 3)

        write_uint32(
            h_preimage, tx.version | OVERWINTERED
        )  # 1. nVersion | fOverwintered
        write_uint32(h_preimage, tx.version_group_id)  # 2. nVersionGroupId
        # 3. hashPrevouts
        write_bytes_fixed(
            h_preimage, bytearray(self.get_prevouts_hash(coin)), TX_HASH_SIZE
        )
        # 4. hashSequence
        write_bytes_fixed(
            h_preimage, bytearray(self.get_sequence_hash(coin)), TX_HASH_SIZE
        )
        # 5. hashOutputs
        write_bytes_fixed(
            h_preimage, bytearray(self.get_outputs_hash(coin)), TX_HASH_SIZE
        )
        # 6. hashJoinSplits
        write_bytes_fixed(h_preimage, b"\x00" * TX_HASH_SIZE, TX_HASH_SIZE)
        write_uint32(h_preimage, tx.lock_time)  # 7. nLockTime
        write_uint32(h_preimage, tx.expiry)  # 8. expiryHeight
        write_uint32(h_preimage, sighash)  # 9. nHashType

        write_bytes_reversed(h_preimage, txi.prev_hash, TX_HASH_SIZE)  # 10a. outpoint
        write_uint32(h_preimage, txi.prev_index)

        script_code = derive_script_code(txi, pubkeyhash)  # 10b. scriptCode
        write_bytes_prefixed(h_preimage, script_code)

        write_uint64(h_preimage, txi.amount)  # 10c. value

        write_uint32(h_preimage, txi.sequence)  # 10d. nSequence

        return get_tx_hash(h_preimage)


class Zip243(Zip143):
    def __init__(self, branch_id: int) -> None:
        super().__init__(branch_id)

    def preimage_hash(
        self,
        coin: CoinInfo,
        tx: SignTx,
        txi: TxInputType,
        pubkeyhash: bytes,
        sighash: int,
    ) -> bytes:
        h_preimage = HashWriter(
            blake2b(
                outlen=32, personal=b"ZcashSigHash" + struct.pack("<I", self.branch_id)
            )
        )

        ensure(coin.overwintered)
        ensure(tx.version == 4)

        write_uint32(
            h_preimage, tx.version | OVERWINTERED
        )  # 1. nVersion | fOverwintered
        write_uint32(h_preimage, tx.version_group_id)  # 2. nVersionGroupId
        # 3. hashPrevouts
        write_bytes_fixed(
            h_preimage, bytearray(self.get_prevouts_hash(coin)), TX_HASH_SIZE
        )
        # 4. hashSequence
        write_bytes_fixed(
            h_preimage, bytearray(self.get_sequence_hash(coin)), TX_HASH_SIZE
        )
        # 5. hashOutputs
        write_bytes_fixed(
            h_preimage, bytearray(self.get_outputs_hash(coin)), TX_HASH_SIZE
        )

        zero_hash = b"\x00" * TX_HASH_SIZE
        write_bytes_fixed(h_preimage, zero_hash, TX_HASH_SIZE)  # 6. hashJoinSplits
        write_bytes_fixed(h_preimage, zero_hash, TX_HASH_SIZE)  # 7. hashShieldedSpends
        write_bytes_fixed(h_preimage, zero_hash, TX_HASH_SIZE)  # 8. hashShieldedOutputs

        write_uint32(h_preimage, tx.lock_time)  # 9. nLockTime
        write_uint32(h_preimage, tx.expiry)  # 10. expiryHeight
        write_uint64(h_preimage, 0)  # 11. valueBalance
        write_uint32(h_preimage, sighash)  # 12. nHashType

        write_bytes_reversed(h_preimage, txi.prev_hash, TX_HASH_SIZE)  # 13a. outpoint
        write_uint32(h_preimage, txi.prev_index)

        script_code = derive_script_code(txi, pubkeyhash)  # 13b. scriptCode
        write_bytes_prefixed(h_preimage, script_code)

        write_uint64(h_preimage, txi.amount)  # 13c. value

        write_uint32(h_preimage, txi.sequence)  # 13d. nSequence

        return get_tx_hash(h_preimage)


class Overwintered(Bitcoinlike):
    def initialize(self, tx: SignTx, keychain: Keychain, coin: CoinInfo) -> None:
        ensure(coin.overwintered)
        super().initialize(tx, keychain, coin)

    def create_hash143(self) -> Bip143:
        if self.tx.version == 3:
            branch_id = self.tx.branch_id or 0x5BA81B19  # Overwinter
            return Zip143(branch_id)  # ZIP-0143 transaction hashing
        elif self.tx.version == 4:
            branch_id = self.tx.branch_id or 0x76B809BB  # Sapling
            return Zip243(branch_id)  # ZIP-0243 transaction hashing
        else:
            raise SigningError(
                FailureType.DataError,
                "Unsupported version for overwintered transaction",
            )

    async def process_nonsegwit_input(self, i: int, txi: TxInputType) -> None:
        await self.process_bip143_input(i, txi)

    async def sign_nonsegwit_input(self, i_sign: int) -> None:
        await self.sign_bip143_input(i_sign)

    def write_tx_header(
        self, w: Writer, tx: Union[SignTx, TransactionType], has_segwit: bool
    ) -> None:
        # nVersion | fOverwintered
        write_uint32(w, tx.version | OVERWINTERED)
        write_uint32(w, tx.version_group_id)  # nVersionGroupId

    def write_sign_tx_footer(self, w: Writer) -> None:
        write_uint32(w, self.tx.lock_time)

        if self.tx.version == 3:
            write_uint32(w, self.tx.expiry)  # expiryHeight
            write_varint(w, 0)  # nJoinSplit
        elif self.tx.version == 4:
            write_uint32(w, self.tx.expiry)  # expiryHeight
            write_uint64(w, 0)  # valueBalance
            write_varint(w, 0)  # nShieldedSpend
            write_varint(w, 0)  # nShieldedOutput
            write_varint(w, 0)  # nJoinSplit
        else:
            raise SigningError(
                FailureType.DataError,
                "Unsupported version for overwintered transaction",
            )
