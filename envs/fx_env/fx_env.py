# coding=utf-8
import datetime
import os

import gym
import numpy
import pandas
from dateutil import relativedelta
from gym import spaces


class FxEnv(gym.Env):
    metadata = {'render.modes': ['human', 'ohlc_array']}

    def __init__(self):
        # 定数
        self.STAY = 0
        self.BUY = 1
        self.SELL = 2
        self.CLOSE = 3
        # 対象となる通貨ペアの最大値
        self.MAX_VALUE = 2
        # 初期の口座資金
        self.initial_balance = 10000
        # CSVファイルのパス配列(最低4ヶ月分を昇順で)
        self.csv_file_paths = []
        now = datetime.datetime.now()
        for _ in range(4):
            now = now - relativedelta.relativedelta(months=1)
            filename = 'DAT_MT_EURUSD_M1_{}.csv'.format(now.strftime('%Y%m'))
            if not os.path.exists(filename):
                print('ファイルが存在していません。下記からダウンロードしてください。', filename)
                print('http://www.histdata.com/download-free-forex-historical-data/?/metatrader/1-minute-bar-quotes/EURUSD/')
            else:
                self.csv_file_paths.append(filename)
        # スプレッド
        self.spread = 0.5
        # Point(1pipsの値)
        self.point = 0.0001
        # 利食いpips
        self.take_profit_pips = 30
        # 損切りpips
        self.stop_loss_pips = 15
        # ロット数
        self.lots = 0.01
        # ロット数
        self.lot_base = 100000
        # 0～3のアクション。定数に詳細は記載している
        self.action_space = gym.spaces.Discrete(4)
        # 観測できる足数
        self.visible_bar = 32
        # 1分足、5分足、30分足、4時間足の5時系列データを足数分作る
        self._reset()
        self.observation_space = spaces.Box(low=0, high=self.MAX_VALUE, shape=numpy.shape(self.make_obs('ohlc_array')))
        self.m5 = []
        self.m30 = []
        self.h4 = []

    def _reset(self):
        self.info = AccountInformation(self.initial_balance)
        # CSVを読み込む
        self.data = pandas.DataFrame()
        for path in self.csv_file_paths:
            csv = pandas.read_csv(path,
                                  names=['date', 'time', 'open', 'high', 'low', 'close', 'v'],
                                  parse_dates={'datetime': ['date', 'time']},
                                  )
            csv.index = csv['datetime']
            csv = csv.drop('datetime', axis=1)
            csv = csv.drop('v', axis=1)
            self.data = self.data.append(csv)
            # 最後に読んだCSVのインデックスを開始インデックスとする
            self.read_index = len(self.data) - len(csv)
        # そこから開始位置をランダムにずらす(5日分(7220分)は残す)
        # self.read_index += numpy.random.randint(0, (len(csv) - 7220))
        # チケット一覧
        self.tickets = []
        return self.make_obs('ohlc_array')

    def _step(self, action):
        current_data = self.data.iloc[self.read_index]
        ask = current_data['close'] + self.spread * self.point
        bid = current_data['close'] - self.spread * self.point

        if action == self.STAY:
            for ticket in self.tickets:
                if ticket.order_type == self.BUY:
                    if bid > ticket.take_profit:
                        # 買いチケットを利確
                        profit = (ticket.take_profit - ticket.open_price) * ticket.lots * self.lot_base
                        self.info.balance += profit
                        self.info.total_pips_buy += profit
                    elif bid < ticket.stop_loss:
                        # 買いチケットを損切り
                        profit = (ticket.stop_loss - ticket.open_price) * ticket.lots * self.lot_base
                        self.info.balance += profit
                        self.info.total_pips_buy += profit
                elif ticket.order_type == self.SELL:
                    if ask < ticket.take_profit:
                        # 売りチケットを利確
                        profit = (ticket.open_price - ticket.take_profit) * ticket.lots * self.lot_base
                        self.info.balance += profit
                        self.info.total_pips_sell += profit
                    elif bid < ticket.stop_loss:
                        # 売りチケットを損切り
                        profit = (ticket.open_price - ticket.stop_loss) * ticket.lots * self.lot_base
                        self.info.balance += profit
                        self.info.total_pips_sell += profit
        elif action == self.BUY:
            ticket = Ticket(self.BUY, ask, ask + self.take_profit_pips * self.point,
                            ask - self.stop_loss_pips * self.point, self.lots)
            self.tickets.append(ticket)
            pass
        elif action == self.SELL:
            ticket = Ticket(self.SELL, bid, bid - self.take_profit_pips * self.point,
                            bid + self.stop_loss_pips * self.point, self.lots)
            self.tickets.append(ticket)
            pass
        elif action == self.CLOSE:
            for ticket in self.tickets:
                if ticket.order_type == self.BUY:
                    # 買いチケットをクローズ
                    profit = (bid - ticket.open_price) * ticket.lots * self.lot_base
                    self.info.balance += profit
                    self.info.total_pips_buy += profit
                elif ticket.order_type == self.SELL:
                    # 売りチケットをクローズ
                    profit = (ticket.open_price - ask) * ticket.lots * self.lot_base
                    self.info.balance += profit
                    self.info.total_pips_sell += profit

        # インデックスをインクリメント
        self.read_index += 1
        # obs, reward, done, infoを返す
        return self.make_obs('ohlc_array'), self.info.total_pips_buy + self.info.total_pips_sell, self.info.balance <= 0 or self.read_index >= len(self.data), self.info

    def _render(self, mode='human', close=False):
        return self.make_obs(mode)

    def make_obs(self, mode):
        """
        1分足、5分足、30分足、4時間足の4時系列データを64本分作成する
        :return:
        """
        target = self.data.iloc[self.read_index - 60 * 4 * 70: self.read_index]
        if mode == 'human':
            m1 = numpy.array(target.iloc[-1 * self.visible_bar:][target.columns])
            m5 = numpy.array(target.resample('5min').agg({'open': 'first',
                                                          'high': 'max',
                                                          'low': 'min',
                                                          'close': 'last'}).dropna().iloc[-1 * self.visible_bar:][target.columns])
            m30 = numpy.array(target.resample('30min').agg({'open': 'first',
                                                            'high': 'max',
                                                            'low': 'min',
                                                            'close': 'last'}).dropna().iloc[-1 * self.visible_bar:][target.columns])
            h4 = numpy.array(target.resample('4H').agg({'open': 'first',
                                                        'high': 'max',
                                                        'low': 'min',
                                                        'close': 'last'}).dropna().iloc[-1 * self.visible_bar:][target.columns])
            return numpy.array([m1, m5, m30, h4])
            # # humanの場合はmatplotlibでチャートのimgを作成する?
            # fig = plt.figure(figsize=(10, 4))
            # # ローソク足は全横幅の太さが1である。表示する足数で割ってさらにその1/3の太さにする
            # width = 1.0 / 64 / 3
            # # 1分足
            # ax = plt.subplot(2, 2, 1)
            # # y軸のオフセット表示を無効にする。
            # ax.get_yaxis().get_major_formatter().set_useOffset(False)
            # data = target.iloc[-1 * self.visible_bar:].values
            # mpf.candlestick_ohlc(ax, data, width=width, colorup='g', colordown='r')
            # # 5分足
            # ax = plt.subplot(2, 2, 2)
            # ax.get_yaxis().get_major_formatter().set_useOffset(False)
            # data = target['close'].resample('5min').ohlc().dropna().iloc[-1 * self.visible_bar:].values
            # mpf.candlestick_ohlc(ax, data, width=width, colorup='g', colordown='r')
            # # 30分足
            # ax = plt.subplot(2, 2, 3)
            # ax.get_yaxis().get_major_formatter().set_useOffset(False)
            # data = target['close'].resample('30min').ohlc().dropna().iloc[-1 * self.visible_bar:].values
            # mpf.candlestick_ohlc(ax, data, width=width, colorup='g', colordown='r')
            # # 4時間足
            # ax = plt.subplot(2, 2, 4)
            # ax.get_yaxis().get_major_formatter().set_useOffset(False)
            # data = target['close'].resample('4H').ohlc().dropna().iloc[-1 * self.visible_bar:].values
            # mpf.candlestick_ohlc(ax, data, width=width, colorup='g', colordown='r')
            # return fig.canvas.buffer_rgba()
        elif mode == 'ohlc_array':
            m1 = numpy.array(target.iloc[-1 * self.visible_bar:][target.columns])
            m5 = numpy.array(target.resample('5min').agg({'open': 'first',
                                                          'high': 'max',
                                                          'low': 'min',
                                                          'close': 'last'}).dropna().iloc[-1 * self.visible_bar:][target.columns])
            m30 = numpy.array(target.resample('30min').agg({'open': 'first',
                                                            'high': 'max',
                                                            'low': 'min',
                                                            'close': 'last'}).dropna().iloc[-1 * self.visible_bar:][target.columns])
            h4 = numpy.array(target.resample('4H').agg({'open': 'first',
                                                        'high': 'max',
                                                        'low': 'min',
                                                        'close': 'last'}).dropna().iloc[-1 * self.visible_bar:][target.columns])
            return numpy.array([m1, m5, m30, h4])


class AccountInformation(object):
    """
    口座情報クラス
    """

    def __init__(self, initial_balance):
        # 口座資金(含み益含む)
        self.balance = initial_balance
        # 口座資金
        self.fixed_balance = initial_balance
        # 総獲得pips(買い)
        self.total_pips_buy = 0
        # 総獲得pips(売り)
        self.total_pips_sell = 0

    def items(self):
        '''
        rl\core.py line 172 で呼び出される
        :return: 口座情報
        '''
        return [('balance', self.balance), ('fixed_balance', self.fixed_balance), ('total_pips_buy', self.total_pips_buy), ('total_pips_sell', self.total_pips_sell)]


class Ticket(object):
    """
    チケット
    """

    def __init__(self, order_type, open_price, take_profit, stop_loss, lots):
        # タイプ
        self.order_type = order_type
        # 約定価格
        self.open_price = open_price
        # 利食い価格
        self.take_profit = take_profit
        # 損切り価格
        self.stop_loss = stop_loss
        # ロット
        self.lots = lots
