import pytest
from blspy import AugSchemeMPL

from chia.consensus.block_rewards import calculate_pool_reward, calculate_base_farmer_reward
from chia.simulator.simulator_protocol import FarmNewBlockProtocol
from chia.types.blockchain_format.program import Program
from chia.types.peer_info import PeerInfo
from chia.types.spend_bundle import SpendBundle
from chia.util.ints import uint16, uint32, uint64

from chia.wallet.util.wallet_types import WalletType
from chia.wallet.did_wallet.did_wallet import DIDWallet
from tests.time_out_assert import time_out_assert, time_out_assert_not_none

# pytestmark = pytest.mark.skip("TODO: Fix tests")


async def get_wallet_num(wallet_manager):
    return len(await wallet_manager.get_all_wallet_info_entries())


class TestDIDWallet:
    @pytest.mark.asyncio
    async def test_creation_from_backup_file(self, self_hostname, three_wallet_nodes):
        num_blocks = 5
        full_nodes, wallets = three_wallet_nodes
        full_node_api = full_nodes[0]
        full_node_server = full_node_api.server
        wallet_node_0, server_0 = wallets[0]
        wallet_node_1, server_1 = wallets[1]
        wallet_node_2, server_2 = wallets[2]
        wallet_0 = wallet_node_0.wallet_state_manager.main_wallet
        wallet_1 = wallet_node_1.wallet_state_manager.main_wallet
        wallet_2 = wallet_node_2.wallet_state_manager.main_wallet

        ph = await wallet_0.get_new_puzzlehash()
        ph1 = await wallet_1.get_new_puzzlehash()
        ph2 = await wallet_2.get_new_puzzlehash()

        await server_0.start_client(PeerInfo(self_hostname, uint16(full_node_server._port)), None)
        await server_1.start_client(PeerInfo(self_hostname, uint16(full_node_server._port)), None)
        await server_2.start_client(PeerInfo(self_hostname, uint16(full_node_server._port)), None)

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        funds = sum(
            [
                calculate_pool_reward(uint32(i)) + calculate_base_farmer_reward(uint32(i))
                for i in range(1, num_blocks - 1)
            ]
        )

        await time_out_assert(10, wallet_0.get_unconfirmed_balance, funds)
        await time_out_assert(10, wallet_0.get_confirmed_balance, funds)
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph1))
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph2))

        # Wallet1 sets up DIDWallet1 without any backup set
        async with wallet_node_0.wallet_state_manager.lock:
            did_wallet_0: DIDWallet = await DIDWallet.create_new_did_wallet(
                wallet_node_0.wallet_state_manager, wallet_0, uint64(101)
            )

        spend_bundle_list = await wallet_node_0.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(wallet_0.id())

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        await time_out_assert(15, did_wallet_0.get_confirmed_balance, 101)
        await time_out_assert(15, did_wallet_0.get_unconfirmed_balance, 101)
        await time_out_assert(15, did_wallet_0.get_pending_change_balance, 0)
        # Wallet1 sets up DIDWallet_1 with DIDWallet_0 as backup
        backup_ids = [bytes.fromhex(did_wallet_0.get_my_DID())]

        async with wallet_node_1.wallet_state_manager.lock:
            did_wallet_1: DIDWallet = await DIDWallet.create_new_did_wallet(
                wallet_node_1.wallet_state_manager, wallet_1, uint64(201), backup_ids
            )

        spend_bundle_list = await wallet_node_1.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(wallet_1.id())

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        await time_out_assert(15, did_wallet_1.get_confirmed_balance, 201)
        await time_out_assert(15, did_wallet_1.get_unconfirmed_balance, 201)
        await time_out_assert(15, did_wallet_1.get_pending_change_balance, 0)

        filename = "test.backup"
        did_wallet_1.create_backup(filename)

        # Wallet2 recovers DIDWallet2 to a new set of keys
        async with wallet_node_2.wallet_state_manager.lock:
            did_wallet_2 = await DIDWallet.create_new_did_wallet_from_recovery(
                wallet_node_2.wallet_state_manager, wallet_2, filename
            )
        coins = await did_wallet_1.select_coins(1)
        coin = coins.copy().pop()
        assert did_wallet_2.did_info.temp_coin == coin
        newpuzhash = await did_wallet_2.get_new_did_inner_hash()
        pubkey = bytes(
            (await did_wallet_2.wallet_state_manager.get_unused_derivation_record(did_wallet_2.wallet_info.id)).pubkey
        )
        message_spend_bundle = await did_wallet_0.create_attestment(
            did_wallet_2.did_info.temp_coin.name(), newpuzhash, pubkey, "test.attest"
        )
        spend_bundle_list = await wallet_node_0.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(
            did_wallet_0.id()
        )

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())
        print(f"pubkey: {pubkey}")

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        (
            test_info_list,
            test_message_spend_bundle,
        ) = await did_wallet_2.load_attest_files_for_recovery_spend(["test.attest"])
        assert message_spend_bundle == test_message_spend_bundle

        spend_bundle = await did_wallet_2.recovery_spend(
            did_wallet_2.did_info.temp_coin,
            newpuzhash,
            test_info_list,
            pubkey,
            test_message_spend_bundle,
        )
        print(f"pubkey: {did_wallet_2}")

        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        await time_out_assert(45, did_wallet_2.get_confirmed_balance, 201)
        await time_out_assert(45, did_wallet_2.get_unconfirmed_balance, 201)

        some_ph = 32 * b"\2"
        await did_wallet_2.create_exit_spend(some_ph)

        spend_bundle_list = await wallet_node_2.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(
            did_wallet_2.id()
        )

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        async def get_coins_with_ph():
            coins = await full_node_api.full_node.coin_store.get_coin_records_by_puzzle_hash(True, some_ph)
            if len(coins) == 1:
                return True
            return False

        await time_out_assert(15, get_coins_with_ph, True)
        await time_out_assert(45, did_wallet_2.get_confirmed_balance, 0)
        await time_out_assert(45, did_wallet_2.get_unconfirmed_balance, 0)

    @pytest.mark.asyncio
    async def test_did_recovery_with_multiple_backup_dids(self, self_hostname, two_wallet_nodes):
        num_blocks = 5
        full_nodes, wallets = two_wallet_nodes
        full_node_api = full_nodes[0]
        server_1 = full_node_api.server
        wallet_node, server_2 = wallets[0]
        wallet_node_2, server_3 = wallets[1]
        wallet = wallet_node.wallet_state_manager.main_wallet
        wallet2 = wallet_node_2.wallet_state_manager.main_wallet

        ph = await wallet.get_new_puzzlehash()

        await server_2.start_client(PeerInfo(self_hostname, uint16(server_1._port)), None)
        await server_3.start_client(PeerInfo(self_hostname, uint16(server_1._port)), None)

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        funds = sum(
            [
                calculate_pool_reward(uint32(i)) + calculate_base_farmer_reward(uint32(i))
                for i in range(1, num_blocks - 1)
            ]
        )

        await time_out_assert(15, wallet.get_confirmed_balance, funds)

        async with wallet_node.wallet_state_manager.lock:
            did_wallet: DIDWallet = await DIDWallet.create_new_did_wallet(
                wallet_node.wallet_state_manager, wallet, uint64(101)
            )

        spend_bundle_list = await wallet_node.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(wallet.id())

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())

        ph = await wallet2.get_new_puzzlehash()
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        await time_out_assert(15, did_wallet.get_confirmed_balance, 101)
        await time_out_assert(15, did_wallet.get_unconfirmed_balance, 101)

        recovery_list = [bytes.fromhex(did_wallet.get_my_DID())]

        async with wallet_node_2.wallet_state_manager.lock:
            did_wallet_2: DIDWallet = await DIDWallet.create_new_did_wallet(
                wallet_node_2.wallet_state_manager, wallet2, uint64(101), recovery_list
            )

        spend_bundle_list = await wallet_node_2.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(wallet2.id())

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        await time_out_assert(15, did_wallet_2.get_confirmed_balance, 101)
        await time_out_assert(15, did_wallet_2.get_unconfirmed_balance, 101)

        assert did_wallet_2.did_info.backup_ids == recovery_list

        recovery_list.append(bytes.fromhex(did_wallet_2.get_my_DID()))

        async with wallet_node_2.wallet_state_manager.lock:
            did_wallet_3: DIDWallet = await DIDWallet.create_new_did_wallet(
                wallet_node_2.wallet_state_manager, wallet2, uint64(201), recovery_list
            )

        spend_bundle_list = await wallet_node_2.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(wallet2.id())

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())

        ph2 = await wallet.get_new_puzzlehash()
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph2))

        assert did_wallet_3.did_info.backup_ids == recovery_list
        await time_out_assert(15, did_wallet_3.get_confirmed_balance, 201)
        await time_out_assert(15, did_wallet_3.get_unconfirmed_balance, 201)
        coins = await did_wallet_3.select_coins(1)
        coin = coins.pop()

        filename = "test.backup"
        did_wallet_3.create_backup(filename)

        async with wallet_node.wallet_state_manager.lock:
            did_wallet_4 = await DIDWallet.create_new_did_wallet_from_recovery(
                wallet_node.wallet_state_manager,
                wallet,
                filename,
            )
        pubkey = (
            await did_wallet_4.wallet_state_manager.get_unused_derivation_record(did_wallet_2.wallet_info.id)
        ).pubkey
        new_ph = await did_wallet_4.get_new_did_inner_hash()
        message_spend_bundle = await did_wallet.create_attestment(coin.name(), new_ph, pubkey, "test1.attest")
        spend_bundle_list = await wallet_node.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(did_wallet.id())

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())
        message_spend_bundle2 = await did_wallet_2.create_attestment(coin.name(), new_ph, pubkey, "test2.attest")
        spend_bundle_list = await wallet_node_2.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(
            did_wallet_2.id()
        )

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())
        message_spend_bundle = message_spend_bundle.aggregate([message_spend_bundle, message_spend_bundle2])

        (
            test_info_list,
            test_message_spend_bundle,
        ) = await did_wallet_4.load_attest_files_for_recovery_spend(["test1.attest", "test2.attest"])
        assert message_spend_bundle == test_message_spend_bundle

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph2))

        await did_wallet_4.recovery_spend(coin, new_ph, test_info_list, pubkey, message_spend_bundle)
        spend_bundle_list = await wallet_node.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(
            did_wallet_4.id()
        )

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph2))

        await time_out_assert(15, did_wallet_4.get_confirmed_balance, 201)
        await time_out_assert(15, did_wallet_4.get_unconfirmed_balance, 201)
        await time_out_assert(15, did_wallet_3.get_confirmed_balance, 0)
        await time_out_assert(15, did_wallet_3.get_unconfirmed_balance, 0)

    @pytest.mark.asyncio
    async def test_did_recovery_with_empty_set(self, self_hostname, two_wallet_nodes):
        num_blocks = 5
        full_nodes, wallets = two_wallet_nodes
        full_node_api = full_nodes[0]
        server_1 = full_node_api.server
        wallet_node, server_2 = wallets[0]
        wallet_node_2, server_3 = wallets[1]
        wallet = wallet_node.wallet_state_manager.main_wallet

        ph = await wallet.get_new_puzzlehash()

        await server_2.start_client(PeerInfo(self_hostname, uint16(server_1._port)), None)
        await server_3.start_client(PeerInfo(self_hostname, uint16(server_1._port)), None)

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        funds = sum(
            [
                calculate_pool_reward(uint32(i)) + calculate_base_farmer_reward(uint32(i))
                for i in range(1, num_blocks - 1)
            ]
        )

        await time_out_assert(15, wallet.get_confirmed_balance, funds)

        async with wallet_node.wallet_state_manager.lock:
            did_wallet: DIDWallet = await DIDWallet.create_new_did_wallet(
                wallet_node.wallet_state_manager, wallet, uint64(101)
            )

        spend_bundle_list = await wallet_node.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(wallet.id())

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        await time_out_assert(15, did_wallet.get_confirmed_balance, 101)
        await time_out_assert(15, did_wallet.get_unconfirmed_balance, 101)
        coins = await did_wallet.select_coins(1)
        coin = coins.pop()
        info = Program.to([])
        pubkey = (await did_wallet.wallet_state_manager.get_unused_derivation_record(did_wallet.wallet_info.id)).pubkey
        try:
            spend_bundle = await did_wallet.recovery_spend(
                coin, ph, info, pubkey, SpendBundle([], AugSchemeMPL.aggregate([]))
            )
            additions = spend_bundle.additions()
            assert additions == []
        except Exception:
            pass
        else:
            assert False

    @pytest.mark.asyncio
    async def test_did_attest_after_recovery(self, self_hostname, two_wallet_nodes):
        num_blocks = 5
        full_nodes, wallets = two_wallet_nodes
        full_node_api = full_nodes[0]
        server_1 = full_node_api.server
        wallet_node, server_2 = wallets[0]
        wallet_node_2, server_3 = wallets[1]
        wallet = wallet_node.wallet_state_manager.main_wallet
        wallet2 = wallet_node_2.wallet_state_manager.main_wallet
        ph = await wallet.get_new_puzzlehash()

        await server_2.start_client(PeerInfo(self_hostname, uint16(server_1._port)), None)
        await server_3.start_client(PeerInfo(self_hostname, uint16(server_1._port)), None)
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        funds = sum(
            [
                calculate_pool_reward(uint32(i)) + calculate_base_farmer_reward(uint32(i))
                for i in range(1, num_blocks - 1)
            ]
        )

        await time_out_assert(15, wallet.get_confirmed_balance, funds)

        async with wallet_node.wallet_state_manager.lock:
            did_wallet: DIDWallet = await DIDWallet.create_new_did_wallet(
                wallet_node.wallet_state_manager, wallet, uint64(101)
            )
        spend_bundle_list = await wallet_node.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(wallet.id())

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())
        ph2 = await wallet2.get_new_puzzlehash()
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph2))

        await time_out_assert(15, did_wallet.get_confirmed_balance, 101)
        await time_out_assert(15, did_wallet.get_unconfirmed_balance, 101)
        recovery_list = [bytes.fromhex(did_wallet.get_my_DID())]

        async with wallet_node_2.wallet_state_manager.lock:
            did_wallet_2: DIDWallet = await DIDWallet.create_new_did_wallet(
                wallet_node_2.wallet_state_manager, wallet2, uint64(101), recovery_list
            )
        spend_bundle_list = await wallet_node_2.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(wallet2.id())

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())
        ph = await wallet.get_new_puzzlehash()
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))
        await time_out_assert(15, did_wallet_2.get_confirmed_balance, 101)
        await time_out_assert(15, did_wallet_2.get_unconfirmed_balance, 101)
        assert did_wallet_2.did_info.backup_ids == recovery_list

        # Update coin with new ID info
        recovery_list = [bytes.fromhex(did_wallet_2.get_my_DID())]
        await did_wallet.update_recovery_list(recovery_list, uint64(1))
        assert did_wallet.did_info.backup_ids == recovery_list
        await did_wallet.create_update_spend()

        spend_bundle_list = await wallet_node.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(did_wallet.id())

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph2))

        await time_out_assert(15, did_wallet.get_confirmed_balance, 101)
        await time_out_assert(15, did_wallet.get_unconfirmed_balance, 101)

        # DID Wallet 2 recovers into DID Wallet 3 with new innerpuz
        filename = "test.backup"
        did_wallet_2.create_backup(filename)

        async with wallet_node.wallet_state_manager.lock:
            did_wallet_3 = await DIDWallet.create_new_did_wallet_from_recovery(
                wallet_node.wallet_state_manager,
                wallet,
                filename,
            )
        new_ph = await did_wallet_3.get_new_did_inner_hash()
        coins = await did_wallet_2.select_coins(1)
        coin = coins.pop()
        pubkey = (
            await did_wallet_3.wallet_state_manager.get_unused_derivation_record(did_wallet_3.wallet_info.id)
        ).pubkey
        message_spend_bundle = await did_wallet.create_attestment(coin.name(), new_ph, pubkey, "test.attest")
        spend_bundle_list = await wallet_node.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(did_wallet.id())

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph2))

        (
            info,
            message_spend_bundle,
        ) = await did_wallet_3.load_attest_files_for_recovery_spend(["test.attest"])
        await did_wallet_3.recovery_spend(coin, new_ph, info, pubkey, message_spend_bundle)
        spend_bundle_list = await wallet_node.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(
            did_wallet_3.id()
        )

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        await time_out_assert(15, did_wallet_3.get_confirmed_balance, 101)
        await time_out_assert(15, did_wallet_3.get_unconfirmed_balance, 101)

        # DID Wallet 1 recovery spends into DID Wallet 4
        filename = "test.backup"
        did_wallet.create_backup(filename)

        async with wallet_node_2.wallet_state_manager.lock:
            did_wallet_4 = await DIDWallet.create_new_did_wallet_from_recovery(
                wallet_node_2.wallet_state_manager,
                wallet2,
                filename,
            )
        coins = await did_wallet.select_coins(1)
        coin = coins.pop()

        new_ph = await did_wallet_4.get_new_did_inner_hash()
        pubkey = (
            await did_wallet_4.wallet_state_manager.get_unused_derivation_record(did_wallet_4.wallet_info.id)
        ).pubkey
        await did_wallet_3.create_attestment(coin.name(), new_ph, pubkey, "test.attest")
        spend_bundle_list = await wallet_node.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(
            did_wallet_3.id()
        )

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph2))
        (
            test_info_list,
            test_message_spend_bundle,
        ) = await did_wallet_4.load_attest_files_for_recovery_spend(["test.attest"])
        await did_wallet_4.recovery_spend(coin, new_ph, test_info_list, pubkey, test_message_spend_bundle)

        spend_bundle_list = await wallet_node_2.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(
            did_wallet_4.id()
        )

        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())

        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        await time_out_assert(15, did_wallet_4.get_confirmed_balance, 101)
        await time_out_assert(15, did_wallet_4.get_unconfirmed_balance, 101)
        await time_out_assert(15, did_wallet.get_confirmed_balance, 0)
        await time_out_assert(15, did_wallet.get_unconfirmed_balance, 0)

    @pytest.mark.asyncio
    async def test_did_transfer(self, two_wallet_nodes):
        num_blocks = 5
        full_nodes, wallets = two_wallet_nodes
        full_node_api = full_nodes[0]
        server_1 = full_node_api.server
        wallet_node, server_2 = wallets[0]
        wallet_node_2, server_3 = wallets[1]
        wallet = wallet_node.wallet_state_manager.main_wallet
        wallet2 = wallet_node_2.wallet_state_manager.main_wallet
        ph = await wallet.get_new_puzzlehash()

        wallet_node.config["trusted_peers"] = {
            full_node_api.full_node.server.node_id.hex(): full_node_api.full_node.server.node_id.hex()
        }

        wallet_node_2.config["trusted_peers"] = {
            full_node_api.full_node.server.node_id.hex(): full_node_api.full_node.server.node_id.hex()
        }

        await server_2.start_client(PeerInfo("localhost", uint16(server_1._port)), None)
        await server_3.start_client(PeerInfo("localhost", uint16(server_1._port)), None)
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        funds = sum(
            [
                calculate_pool_reward(uint32(i)) + calculate_base_farmer_reward(uint32(i))
                for i in range(1, num_blocks - 1)
            ]
        )

        await time_out_assert(15, wallet.get_confirmed_balance, funds)

        async with wallet_node.wallet_state_manager.lock:
            did_wallet_1: DIDWallet = await DIDWallet.create_new_did_wallet(
                wallet_node.wallet_state_manager, wallet, uint64(101), [bytes(ph)]
            )
        spend_bundle_list = await wallet_node.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(wallet.id())
        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())
        ph2 = await wallet2.get_new_puzzlehash()
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph2))
        await time_out_assert(15, did_wallet_1.get_confirmed_balance, 101)
        await time_out_assert(15, did_wallet_1.get_unconfirmed_balance, 101)
        # Transfer DID
        new_puzhash = await wallet2.get_new_puzzlehash()
        await did_wallet_1.transfer_did(new_puzhash, uint64(0))
        print(f"Original launch_id {did_wallet_1.did_info.origin_coin.name()}")
        spend_bundle_list = await wallet_node.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(
            did_wallet_1.id()
        )
        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())
        ph2 = await wallet2.get_new_puzzlehash()
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph2))
        # Check if the DID wallet is created in the wallet2
        await time_out_assert(60, get_wallet_num, 2, wallet_node_2.wallet_state_manager)

        # Get the new DID wallet
        did_wallets = list(
            filter(
                lambda w: (w.type == WalletType.DISTRIBUTED_ID),
                await wallet_node_2.wallet_state_manager.get_all_wallet_info_entries(),
            )
        )
        did_wallet_2: DIDWallet = wallet_node_2.wallet_state_manager.wallets[did_wallets[0].id]
        assert did_wallet_1.did_info.origin_coin == did_wallet_2.did_info.origin_coin
        assert did_wallet_1.did_info.backup_ids[0] == did_wallet_2.did_info.backup_ids[0]
        assert did_wallet_1.did_info.num_of_backup_ids_needed == did_wallet_2.did_info.num_of_backup_ids_needed

    @pytest.mark.asyncio
    async def test_update_recovery_list(self, two_wallet_nodes):
        num_blocks = 5
        full_nodes, wallets = two_wallet_nodes
        full_node_api = full_nodes[0]
        server_1 = full_node_api.server
        wallet_node, server_2 = wallets[0]
        wallet_node_2, server_3 = wallets[1]
        wallet = wallet_node.wallet_state_manager.main_wallet
        ph = await wallet.get_new_puzzlehash()

        wallet_node.config["trusted_peers"] = {
            full_node_api.full_node.server.node_id.hex(): full_node_api.full_node.server.node_id.hex()
        }

        wallet_node_2.config["trusted_peers"] = {
            full_node_api.full_node.server.node_id.hex(): full_node_api.full_node.server.node_id.hex()
        }

        await server_2.start_client(PeerInfo("localhost", uint16(server_1._port)), None)
        await server_3.start_client(PeerInfo("localhost", uint16(server_1._port)), None)
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        funds = sum(
            [
                calculate_pool_reward(uint32(i)) + calculate_base_farmer_reward(uint32(i))
                for i in range(1, num_blocks - 1)
            ]
        )

        await time_out_assert(15, wallet.get_confirmed_balance, funds)

        async with wallet_node.wallet_state_manager.lock:
            did_wallet_1: DIDWallet = await DIDWallet.create_new_did_wallet(
                wallet_node.wallet_state_manager, wallet, uint64(101), []
            )
        spend_bundle_list = await wallet_node.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(wallet.id())
        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())
        ph2 = await wallet.get_new_puzzlehash()
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph2))
        await time_out_assert(15, did_wallet_1.get_confirmed_balance, 101)
        await time_out_assert(15, did_wallet_1.get_unconfirmed_balance, 101)
        await did_wallet_1.update_recovery_list([bytes(ph)], 1)
        await did_wallet_1.create_update_spend()
        ph2 = await wallet.get_new_puzzlehash()
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph2))
        await time_out_assert(15, did_wallet_1.get_confirmed_balance, 101)
        await time_out_assert(15, did_wallet_1.get_unconfirmed_balance, 101)
        assert did_wallet_1.did_info.backup_ids[0] == bytes(ph)
        assert did_wallet_1.did_info.num_of_backup_ids_needed == 1

    @pytest.mark.asyncio
    async def test_update_metadata(self, two_wallet_nodes):
        num_blocks = 5
        full_nodes, wallets = two_wallet_nodes
        full_node_api = full_nodes[0]
        server_1 = full_node_api.server
        wallet_node, server_2 = wallets[0]
        wallet_node_2, server_3 = wallets[1]
        wallet = wallet_node.wallet_state_manager.main_wallet
        ph = await wallet.get_new_puzzlehash()

        wallet_node.config["trusted_peers"] = {
            full_node_api.full_node.server.node_id.hex(): full_node_api.full_node.server.node_id.hex()
        }

        wallet_node_2.config["trusted_peers"] = {
            full_node_api.full_node.server.node_id.hex(): full_node_api.full_node.server.node_id.hex()
        }

        await server_2.start_client(PeerInfo("localhost", uint16(server_1._port)), None)
        await server_3.start_client(PeerInfo("localhost", uint16(server_1._port)), None)
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph))

        funds = sum(
            [
                calculate_pool_reward(uint32(i)) + calculate_base_farmer_reward(uint32(i))
                for i in range(1, num_blocks - 1)
            ]
        )

        await time_out_assert(15, wallet.get_confirmed_balance, funds)

        async with wallet_node.wallet_state_manager.lock:
            did_wallet_1: DIDWallet = await DIDWallet.create_new_did_wallet(
                wallet_node.wallet_state_manager, wallet, uint64(101), []
            )
        spend_bundle_list = await wallet_node.wallet_state_manager.tx_store.get_unconfirmed_for_wallet(wallet.id())
        spend_bundle = spend_bundle_list[0].spend_bundle
        await time_out_assert_not_none(5, full_node_api.full_node.mempool_manager.get_spendbundle, spend_bundle.name())
        ph2 = await wallet.get_new_puzzlehash()
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph2))
        await time_out_assert(15, did_wallet_1.get_confirmed_balance, 101)
        await time_out_assert(15, did_wallet_1.get_unconfirmed_balance, 101)
        metadata = {}
        metadata["Twitter"] = "http://www.twitter.com"
        await did_wallet_1.update_metadata(metadata)
        await did_wallet_1.create_update_spend()
        ph2 = await wallet.get_new_puzzlehash()
        for i in range(1, num_blocks):
            await full_node_api.farm_new_transaction_block(FarmNewBlockProtocol(ph2))
        await time_out_assert(15, did_wallet_1.get_confirmed_balance, 101)
        await time_out_assert(15, did_wallet_1.get_unconfirmed_balance, 101)
        assert did_wallet_1.did_info.metadata.find("Twitter") > 0
