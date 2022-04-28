from dataclasses import dataclass
from datetime import datetime as dt
from datetime import timedelta as td
from pathlib import Path
from sklearn.decomposition import PCA
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split, GridSearchCV
from sklearn.neighbors import KNeighborsClassifier
from sklearn.preprocessing import LabelEncoder, StandardScaler, MinMaxScaler
import matplotlib.pyplot as plt
import numpy as np
import numpy.random as random
import os
import pandas as pd
import pickle
import pybaseball as bball
import seaborn as sns
import tempfile

bball.cache.enable()
random_state = 42
tmpdir = f'{tempfile.gettempdir()}/PitchGuesser'
if not os.path.exists(tmpdir):
    os.mkdir(tmpdir)

@dataclass
class PitchData:
    """Class for modeling pitch types"""
    start_dt: str = '2022-03-17'  # start day for
    end_dt: str = dt.today().strftime("%Y-%m-%d")
    refresh: bool = False

    def __post_init__(self):
        pdir = Path(__file__).parent.absolute()
        self.datadir = os.path.join(pdir, 'data')
        self.__pkl = f"{tmpdir}/pitch_data.pkl"
        self.pitches = ['4-Seam Fastball', 'Changeup', 'Curveball', 'Cutter', 'Sinker', 'Slider']
        self.__start_ts = dt.strptime(self.start_dt, "%Y-%m-%d")
        self.__end_ts = dt.strptime(self.end_dt, "%Y-%m-%d")
        self.raw_data = self.__get_data()

    def __get_from_date(self, df):
        if self.__start_ts < df.game_date.min():
            df = pd.concat([df, bball.statcast(
                start_dt=self.start_dt,
                end_dt=dt.strftime(df.game_date.min(), "%Y-%m-%d")
            )])
        return df

    def __get_to_date(self, df):
        if self.__end_ts > df.game_date.max() + td(days=1):
            df = pd.concat([df, bball.statcast(
                start_dt=dt.strftime(df.game_date.max(), "%Y-%m-%d"),
                end_dt=self.end_dt
            )])
        return df

    def __clean_data(self, df):
        df = df.dropna(subset=['release_speed', 'release_pos_x', 'release_pos_z'])
        df.to_pickle(self.__pkl)
        df = self.__split_cat(df)
        df = self.__get_cols(df)
        df = df.fillna(method='ffill')
        # most common pitches
        df = df.loc[df.pitch_name.isin(self.pitches), :].sort_values(by=['pitch_name', 'game_date', 'pitcher'])
        return df.loc[(df['game_date'] >= self.__start_ts) & (df['game_date'] <= self.__end_ts), :]

    def __split_cat(self, df):
        df2 = df.copy()
        df2['lefty'] = df.p_throws == 'L'
        df2['righty'] = df.p_throws == 'R'
        df2['ball'] = df.type == 'B'
        df2['strike'] = df.type == 'S'
        df2['hit_in_play'] = df.type == 'X'
        return df2

    def __get_data(self):
        if os.path.exists(self.__pkl) and not self.refresh:
            df = pd.read_pickle(self.__pkl)
        else:
            df = bball.statcast(self.start_dt, self.end_dt)
        df = self.__get_from_date(df)
        df = self.__get_to_date(df)
        return self.__clean_data(df)

    def __get_cols(self, df):
        cols = pd.read_csv(f"{self.datadir}/cols.csv", header=None).squeeze().to_list()
        return df[cols]

@dataclass
class PitchModelBuild(PitchData):
    model = None
    model_pkl = None
    test_size: float = 0.30
    experiment: int = 0

    def __post_init__(self):
        super(PitchModelBuild, self).__post_init__()
        if self.model is None:
            raise ValueError('A model must be passed')
        if self.model_pkl is None:
            raise ValueError('A model pickle file must be passed')
        self.features = self.__feature_types()
        self.__splitter()
        self.__fit()

    def __feature_types(self):
        return {
            'categorical': ['lefty', 'righty', 'ball', 'strike', 'hit_in_play', 'zone'],
            'numeric': [
                'release_speed',
                'release_pos_x',
                'release_pos_z',
                'pfx_x',
                'pfx_z',
                'plate_x',
                'plate_z',
                'vx0',
                'vy0',
                'vz0',
                'ax',
                'ay',
                'az',
                'sz_top',
                'sz_bot',
                'release_spin_rate',
                'release_extension',
                'spin_axis',
            ]
        }

    def __get_exp_model_path(self, exper):
        fname, ext = self.model_pkl.split('.')
        fname = f"{fname}_{exper}"
        return f"{fname}.{ext}"

    def __experiment(self, X):
        if self.experiment == 1:
            self.model_pkl = self.__get_exp_model_path('scaled')
            i = 1
            for j, col in enumerate(X.columns):
                if j % 3 == 0:
                    i += 1
                    X[col] = X[col] + i
                elif j % 3 == 1:
                    X[col] = X[col] * i
                elif j % 3 == 2:
                    X[col] = X[col] ** i
        elif self.experiment == 2:
            self.model_pkl = self.__get_exp_model_path('add_feature')
            X['plate_mag'] = np.sqrt(X.plate_x**2 + X.plate_z**2)
            X['release_pos_mag'] = np.sqrt(X.release_pos_x**2 + X.release_pos_z**2)
            X['pfx_mag'] = np.sqrt(X.pfx_x**2 + X.pfx_z**2)
            X['a_mag'] = np.sqrt(X.ax**2 + X.ay**2 + X.az**2)
            X['v0_mag'] = np.sqrt(X.vx0**2 + X.vy0**2 + X.vz0**2)
        elif self.experiment == 5:
            self.model_pkl = self.__get_exp_model_path('random')
            X['random_cont'] = [random.uniform(100, 300) for _ in range(len(X))]
            X['random_desc'] = random.randint(1, 6, len(X))
        return X

    def __set_x_y(self):
        skip_cols = (
            'game_date',
            'pitcher',
            'player_name',
            'pitch_name',
            'p_throws',
            'type',
        )

        X = self.raw_data.loc[:, ~self.raw_data.columns.isin(skip_cols)].copy()
        X = self.__experiment(X)
        le = LabelEncoder()
        y = self.raw_data.pitch_name
        y = le.fit_transform(y)

        return X, y

    def __splitter(self):

        X, y = self.__set_x_y()

        self.X_train, self.X_test, self.y_train, self.y_test = train_test_split(
            X,
            y,
            test_size=self.test_size,
            random_state=random_state
        )

    def __fit(self):
        if not os.path.exists(self.model_pkl) or self.refresh:
            self.model.fit(self.X_train, self.y_train)
            with open(self.model_pkl, 'wb') as fout:
                pickle.dump(self.model, fout)
        else:
            with open(self.model_pkl, 'rb') as fin:
                self.model = pickle.load(fin)

@dataclass
class PitchGuessPost(PitchModelBuild):

    model_name = ''
    
    def __post_init__(self):
        super(PitchGuessPost, self).__post_init__()
        self.y_predict = self.__prediction()
        self.cm = self.__get_cm()
        self.score = self.__score()

    def show_correlation(self):
        if self.experiment != 3:
            corr_df = self.X_train[self.features['numeric']]
        else:
            corr_df = self.X_train
        cor = corr_df.corr(method='pearson')
        fig, ax = plt.subplots(figsize=(8, 6))
        plt.title("Correlation Plot")
        sns.heatmap(
            cor,
            mask=np.zeros_like(cor, dtype=np.bool_),
            cmap=sns.diverging_palette(220, 10, as_cmap=True),
            square=True,
            ax=ax
        )
        plt.show()
        return cor

    def show_pair_plot(self):
        sns.set()
        fcols = self.features['numeric'] + ['pitch_name']
        df = self.raw_data[fcols].sample(300, replace=False).reset_index(drop=True)
        plt.title("Pair Plot")
        sns.pairplot(df, hue="pitch_name")
        plt.show()

    def __prediction(self):
        return self.model.predict(self.X_test)

    def __get_cm(self):
        return pd.DataFrame(
            confusion_matrix(self.y_test, self.y_predict),
            index=self.pitches,
            columns=self.pitches
        )

    def __score(self):
        return accuracy_score(self.y_test, self.y_predict)

    def class_report(self):
        print(classification_report(self.y_test, self.y_predict))

def _get_grid_search(model, params):
    return GridSearchCV(
        model,
        params,
        cv=2,
        n_jobs=-1,
        verbose=1,
        scoring='f1_micro'
    )

@dataclass
class PitchRFC(PitchGuessPost):
    __params = {
        'n_estimators': [100, 200, 500],
        'criterion': ['gini', 'entropy']
    }
    model_name = 'Random Forest'
    model = _get_grid_search(RandomForestClassifier(random_state=random_state), __params)
    model_pkl = f'{tmpdir}/RFC.pkl'

@dataclass
class PitchGBC(PitchGuessPost):
    # this took too long for gridsearch
    model_name = 'Gradient Boosting'
    model = GradientBoostingClassifier(random_state=random_state)
    model_pkl = f'{tmpdir}/GBC.pkl'

@dataclass
class PitchKNN(PitchGuessPost):
    __params = {
        'n_neighbors': [5, 10, 15],
        'weights': ['uniform', 'distance'],
        'metric': ['euclidean']
    }
    model_name = 'K-Nearest Neighbor'
    model = _get_grid_search(KNeighborsClassifier(), __params)
    model_pkl = f'{tmpdir}/KNN.pkl'

if __name__ == '__main__':
    # base
    #PitchRFC(start_dt='2022-03-17')
    #PitchKNN(start_dt='2022-03-17')
    #PitchGBC(start_dt='2022-03-17')
    ## exp1
    #PitchRFC(start_dt='2022-03-17', experiment=1)
    #PitchKNN(start_dt='2022-03-17', experiment=1)
    #PitchGBC(start_dt='2022-03-17', experiment=1)
    ## exp2
    #PitchRFC(start_dt='2022-03-17', experiment=2)
    #PitchKNN(start_dt='2022-03-17', experiment=2)
    #PitchGBC(start_dt='2022-03-17', experiment=2)
    # exp5
    #PitchRFC(start_dt='2022-03-17', experiment=5)
    #PitchKNN(start_dt='2022-03-17', experiment=5)
    #PitchGBC(start_dt='2022-03-17', experiment=5)
    pass
