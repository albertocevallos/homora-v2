from brownie import accounts, interface, Contract, chain
from brownie import (HomoraBank, ProxyOracle, CoreOracle, UniswapV2Oracle, SimpleOracle,
                     UniswapV2SpellV1, WERC20, WStakingRewards, MockCErc20)
import brownie
from brownie.exceptions import VirtualMachineError
from .utils import *


def almostEqual(a, b):
    thresh = 0.01
    return a <= b + thresh * abs(b) and a >= b - thresh * abs(b)


def setup_bank_hack(homora):
    donator = accounts[5]
    fake = accounts.at(homora.address, force=True)
    controller = interface.IComptroller(
        '0x3d5BC3c8d13dcB8bF317092d84783c2697AE9258')
    creth = interface.ICEtherEx('0xD06527D5e56A3495252A528C4987003b712860eE')
    creth.mint({'value': '90 ether', 'from': donator})
    creth.transfer(fake, creth.balanceOf(donator), {'from': donator})
    controller.enterMarkets([creth], {'from': fake})


def setup_transfer(asset, fro, to, amt):
    print(f'sending from {fro} {amt} {asset.name()} to {to}')
    asset.transfer(to, amt, {'from': fro})


def main():
    admin = accounts[0]

    alice = accounts[1]
    bob = accounts[2]
    dpi = interface.IERC20Ex('0x1494ca1f11d487c2bbe4543e90080aeba4ba3c2b')
    weth = interface.IERC20Ex('0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2')
    usdt = interface.IERC20Ex('0xdac17f958d2ee523a2206206994597c13d831ec7')

    lp = interface.IERC20Ex('0x4d5ef58aac27d99935e5b6b4a6778ff292059991')
    lp_usdt = interface.IERC20Ex('0x0d4a11d5eeaac28ec3f61d100daf4d40471f1852')
    crdpi = MockCErc20.deploy(dpi, {'from': admin})
    crusdt = interface.ICErc20('0x797AAB1ce7c01eB727ab980762bA88e7133d2157')

    router = interface.IUniswapV2Router02(
        '0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D')

    staking = accounts.at('0xB93b505Ed567982E2b6756177ddD23ab5745f309', force=True)
    index = interface.IERC20Ex(interface.IStakingRewardsEx(staking).rewardsToken())

    wstaking = WStakingRewards.deploy(staking, lp, index, {'from': admin})

    werc20 = WERC20.deploy({'from': admin})

    simple_oracle = SimpleOracle.deploy({'from': admin})
    simple_oracle.setETHPx([dpi, weth, usdt], [2**112 * 100 // 700, 2**112, 2**112 // 700])

    uniswap_oracle = UniswapV2Oracle.deploy(simple_oracle, {'from': admin})
    core_oracle = CoreOracle.deploy({'from': admin})
    oracle = ProxyOracle.deploy(core_oracle, {'from': admin})
    oracle.setWhitelistERC1155([werc20, wstaking], True, {'from': admin})
    core_oracle.setRoute(
        [dpi, weth, lp, usdt, lp_usdt],
        [simple_oracle, simple_oracle, uniswap_oracle, simple_oracle, uniswap_oracle],
        {'from': admin},
    )
    oracle.setOracles(
        [dpi, weth, lp, usdt, lp_usdt],
        [
            [10000, 10000, 10000],
            [10000, 10000, 10000],
            [10000, 10000, 10000],
            [10000, 10000, 10000],
            [10000, 10000, 10000],
        ],
        {'from': admin},
    )

    homora = HomoraBank.deploy({'from': admin})
    homora.initialize(oracle, 1000, {'from': admin})  # 10% fee
    setup_bank_hack(homora)
    homora.addBank(dpi, crdpi, {'from': admin})
    homora.addBank(usdt, crusdt, {'from': admin})

    # setup initial funds to alice
    mint_tokens(dpi, alice)
    mint_tokens(weth, alice)
    mint_tokens(usdt, alice)

    # check alice's funds
    print(f'Alice dpi balance {dpi.balanceOf(alice)}')
    print(f'Alice weth balance {weth.balanceOf(alice)}')

    # Steal some LP from the staking pool
    mint_tokens(lp, alice)
    mint_tokens(lp, bob)

    mint_tokens(lp_usdt, alice)
    mint_tokens(lp_usdt, bob)

    # set approval
    dpi.approve(homora, 2**256-1, {'from': alice})
    dpi.approve(crdpi, 2**256-1, {'from': alice})
    weth.approve(homora, 2**256-1, {'from': alice})
    usdt.approve(homora, 2**256-1, {'from': alice})
    usdt.approve(crusdt, 2**256-1, {'from': alice})
    lp.approve(homora, 2**256-1, {'from': alice})
    lp.approve(staking, 2**256-1, {'from': bob})

    uniswap_spell = UniswapV2SpellV1.deploy(
        homora, werc20, router, {'from': admin})
    # first time call to reduce gas
    uniswap_spell.getPair(weth, dpi, {'from': admin})

    #####################################################################################
    print('=========================================================================')
    print('Case 1. add liquidity first time')

    prevABal = dpi.balanceOf(alice)
    prevBBal = weth.balanceOf(alice)
    prevLPBal = lp.balanceOf(alice)
    prevLPBal_bank = lp.balanceOf(homora)
    prevLPBal_staking = lp.balanceOf(staking)

    if interface.IUniswapV2Pair(lp).token0() == dpi:
        prevARes, prevBRes, _ = interface.IUniswapV2Pair(lp).getReserves()
    else:
        prevBRes, prevARes, _ = interface.IUniswapV2Pair(lp).getReserves()

    dpi_amt = 10 * 10**18
    weth_amt = 10**18
    lp_amt = 0
    borrow_dpi_amt = 0
    borrow_weth_amt = 0

    tx = homora.execute(
        0,
        uniswap_spell,
        uniswap_spell.addLiquidityWStakingRewards.encode_input(
            dpi,  # token 0
            weth,  # token 1
            [dpi_amt,  # supply DPI
             weth_amt,   # supply WETH
             lp_amt,  # supply LP
             borrow_dpi_amt,  # borrow DPI
             borrow_weth_amt,  # borrow WETH
             0,  # borrow LP tokens
             0,  # min DPI
             0],  # min WETH
            wstaking,
        ),
        {'from': alice}
    )

    curABal = dpi.balanceOf(alice)
    curBBal = weth.balanceOf(alice)
    curLPBal = lp.balanceOf(alice)
    curLPBal_bank = lp.balanceOf(homora)
    curLPBal_staking = lp.balanceOf(staking)

    if interface.IUniswapV2Pair(lp).token0() == dpi:
        curARes, curBRes, _ = interface.IUniswapV2Pair(lp).getReserves()
    else:
        curBRes, curARes, _ = interface.IUniswapV2Pair(lp).getReserves()

    print('spell lp balance', lp.balanceOf(uniswap_spell))
    print('Alice delta A balance', curABal - prevABal)
    print('Alice delta B balance', curBBal - prevBBal)
    print('add liquidity gas', tx.gas_used)
    print('bank lp balance', curLPBal_bank)

    _, _, _, totalDebt, totalShare = homora.getBankInfo(dpi)
    print('bank dpi totalDebt', totalDebt)
    print('bank dpi totalShare', totalShare)

    print('bank prev LP balance', prevLPBal_bank)
    print('bank cur LP balance', curLPBal_bank)

    print('staking prev LP balance', prevLPBal_staking)
    print('staking cur LP balance', curLPBal_staking)

    print('prev dpi res', prevARes)
    print('cur dpi res', curARes)

    print('prev weth res', prevBRes)
    print('cur weth res', curBRes)

    # alice
    assert almostEqual(curABal - prevABal, -dpi_amt), 'incorrect DPI amt'
    assert almostEqual(curBBal - prevBBal, -weth_amt), 'incorrect WETH amt'
    assert curLPBal - prevLPBal == -lp_amt, 'incorrect LP amt'

    # spell
    assert dpi.balanceOf(uniswap_spell) == 0, 'non-zero spell DPI balance'
    assert weth.balanceOf(uniswap_spell) == 0, 'non-zero spell WETH balance'
    assert lp.balanceOf(uniswap_spell) == 0, 'non-zero spell LP balance'
    assert totalDebt == borrow_dpi_amt

    # check balance and pool reserves
    assert curABal - prevABal - borrow_dpi_amt == - \
        (curARes - prevARes), 'not all DPI tokens go to LP pool'
    assert curBBal - prevBBal - borrow_weth_amt == - \
        (curBRes - prevBRes), 'not all WETH tokens go to LP pool'

    _, _, collId, collSize = homora.getPositionInfo(1)
    print('collSize', collSize)

    # staking directly
    prevIndex = index.balanceOf(bob)
    print('bob lp balance', interface.IERC20Ex(lp).balanceOf(bob))
    tx = interface.IStakingRewards(staking).stake(collSize, {'from': bob})

    chain.sleep(20000)

    prevAliceIndexBalance = index.balanceOf(alice)
    print('Alice index balance', prevAliceIndexBalance)

    #####################################################################################
    print('=========================================================================')
    print('Case 2. add liquidity (failed tx desired)')

    usdt_amt = 10 * 10**6
    weth_amt = 10**18
    lp_amt = 0
    borrow_usdt_amt = 0
    borrow_weth_amt = 0

    try:
        tx = homora.execute(
            1,
            uniswap_spell,
            uniswap_spell.addLiquidityWStakingRewards.encode_input(
                usdt,  # token 0
                weth,  # token 1
                [usdt_amt,  # supply USDT
                    weth_amt,   # supply WETH
                    lp_amt,  # supply LP
                    borrow_usdt_amt,  # borrow USDT
                    borrow_weth_amt,  # borrow WETH
                    0,  # borrow LP tokens
                    0,  # min USDT
                    0],  # min WETH
                wstaking,
            ),
            {'from': alice}
        )
    except VirtualMachineError:
        pass

    #####################################################################################
    print('=========================================================================')
    print('Case 3. remove liquidity (failed tx desired)')

    lp_take_amt = collSize
    lp_want = 0
    usdt_repay = 0
    weth_repay = 0

    try:
        tx = homora.execute(
            1,
            uniswap_spell,
            uniswap_spell.removeLiquidityWStakingRewards.encode_input(
                usdt,  # token 0
                weth,  # token 1
                [lp_take_amt,  # take out LP tokens
                    lp_want,   # withdraw LP tokens to wallet
                    usdt_repay,  # repay USDT
                    weth_repay,  # repay WETH
                    0,  # repay LP tokens
                    0,  # min USDT
                    0],  # min WETH
                wstaking,
            ),
            {'from': alice}
        )
    except VirtualMachineError:
        pass

    #####################################################################################
    print('=========================================================================')
    print('Case 4. remove liquidity')

    prevABal = dpi.balanceOf(alice)
    prevBBal = weth.balanceOf(alice)
    prevLPBal = lp.balanceOf(alice)
    prevLPBal_bank = lp.balanceOf(homora)
    prevLPBal_staking = lp.balanceOf(staking)
    prevETHBal = alice.balance()

    if interface.IUniswapV2Pair(lp).token0() == dpi:
        prevARes, prevBRes, _ = interface.IUniswapV2Pair(lp).getReserves()
    else:
        prevBRes, prevARes, _ = interface.IUniswapV2Pair(lp).getReserves()

    lp_take_amt = collSize
    lp_want = 0
    dpi_repay = 0
    weth_repay = 0

    tx = homora.execute(
        1,
        uniswap_spell,
        uniswap_spell.removeLiquidityWStakingRewards.encode_input(
            dpi,  # token 0
            weth,  # token 1
            [lp_take_amt,  # take out LP tokens
             lp_want,   # withdraw LP tokens to wallet
             dpi_repay,  # repay DPI
             weth_repay,  # repay WETH
             0,  # repay LP tokens
             0,  # min DPI
             0],  # min WETH
            wstaking,
        ),
        {'from': alice}
    )

    curABal = dpi.balanceOf(alice)
    curBBal = weth.balanceOf(alice)
    curLPBal = lp.balanceOf(alice)
    curLPBal_bank = lp.balanceOf(homora)
    curLPBal_staking = lp.balanceOf(staking)
    curETHBal = alice.balance()

    if interface.IUniswapV2Pair(lp).token0() == dpi:
        curARes, curBRes, _ = interface.IUniswapV2Pair(lp).getReserves()
    else:
        curBRes, curARes, _ = interface.IUniswapV2Pair(lp).getReserves()

    print('spell lp balance', lp.balanceOf(uniswap_spell))
    print('Alice delta A balance', curABal - prevABal)
    print('Alice delta B balance', curBBal - prevBBal)
    print('add liquidity gas', tx.gas_used)
    print('bank lp balance', curLPBal_bank)

    _, _, _, totalDebt, totalShare = homora.getBankInfo(dpi)
    print('bank dpi totalDebt', totalDebt)
    print('bank dpi totalShare', totalShare)

    print('bank prev LP balance', prevLPBal_bank)
    print('bank cur LP balance', curLPBal_bank)

    print('wstaking prev LP balance', prevLPBal_staking)
    print('wstaking cur LP balance', curLPBal_staking)

    print('prev dpi res', prevARes)
    print('cur dpi res', curARes)

    print('prev weth res', prevBRes)
    print('cur weth res', curBRes)

    # alice
    assert almostEqual(curBBal - prevBBal, 0), 'incorrect WETH amt'
    assert almostEqual(curLPBal - prevLPBal, lp_want), 'incorrect LP amt'

    # staking
    assert almostEqual(curLPBal_staking - prevLPBal_staking, -
                       lp_take_amt), 'incorrect staking LP amt'

    # spell
    assert dpi.balanceOf(uniswap_spell) == 0, 'non-zero spell DPI balance'
    assert weth.balanceOf(uniswap_spell) == 0, 'non-zero spell WETH balance'
    assert lp.balanceOf(uniswap_spell) == 0, 'non-zero spell LP balance'

    # check balance and pool reserves
    assert almostEqual(curABal - prevABal + dpi_repay, -
                       (curARes - prevARes)), 'inconsistent DPI from withdraw'
    assert almostEqual(curBBal - prevBBal, 0), 'inconsistent WETH from withdraw'
    assert almostEqual(curETHBal - prevETHBal + weth_repay, -
                       (curBRes - prevBRes)), 'inconsistent ETH from withdraw'

    curAliceIndexBalance = index.balanceOf(alice)
    print('Alice index balance', curAliceIndexBalance)
    receivedIndex = curAliceIndexBalance - prevAliceIndexBalance
    print('received index', receivedIndex)

    # check with staking directly
    tx = interface.IStakingRewards(staking).getReward({'from': bob})
    receivedIndexFromStaking = index.balanceOf(bob) - prevIndex
    print('receivedIndexFromStaking', receivedIndexFromStaking)
    assert almostEqual(receivedIndex, receivedIndexFromStaking)
