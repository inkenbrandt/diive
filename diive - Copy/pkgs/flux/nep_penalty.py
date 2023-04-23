from pathlib import Path
from typing import Literal

import matplotlib as mpl
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import dates as mdates
from pandas import DataFrame

import diive.core.dfun.frames as frames
from diive.core.dfun.fits import BinFitterCP
from diive.core.plotting.fitplot import fitplot
from diive.core.plotting.plotfuncs import default_legend, default_format, nice_date_ticks, save_fig
from diive.core.plotting.styles.LightTheme import COLOR_NEP, COLOR_RECO
from diive.pkgs.createvar.vpd import calc_vpd_from_ta_rh
from diive.pkgs.gapfilling.randomforest_ts import RandomForestTS


class NEPpenalty:

    def __init__(
            self,
            df: DataFrame,
            vpd_col: str,
            nep_col: str,
            swin_col: str,
            ta_col: str,
            rh_col: str,
            thres_crd: float,
            # thres_xcrd: float,
            thres_ncrd_lower: float,
            penalty_start_month: int = 5,
            penalty_end_month: int = 9,
            **random_forest_params
    ):
        """

        Args:
            df: Timeseries data with timestamp as index in half-hourly time resolution
            vpd_col: VPD column name, is used to define critical conditions (kPa)
            nep_col: NEP column name (umol CO2 m-2 s-1) (NEP=-NEE)
            swin_col: Short-wave incoming radiation column name (W m-2)
            ta_col: Air temperature column name (°C)
            rh_col: Relative humidity column name (%)
            thres_crd: Critical threshold
                Threshold of *x_col* above which days are defined as critical.
            thres_ncrd_lower: Lower near-critical threshold
                Threshold of *x_col* above which days are defined as near-critical.
                *thres_ncrd_lower* <= near-critical days <= *thres_crd*
        """
        # Columns
        self.df = df.copy()
        self.vpd_col = vpd_col
        self.nep_col = nep_col
        self.swin_col = swin_col
        self.ta_col = ta_col
        self.rh_col = rh_col
        self.thres_crd = thres_crd
        # self.thres_xcrd = thres_xcrd
        self.thres_ncrd_lower = thres_ncrd_lower
        self.penalty_start_month = penalty_start_month
        self.penalty_end_month = penalty_end_month
        self.random_forest_params = random_forest_params

        # Convert NEE units: umol CO2 m-2 s-1 --> g CO2 m-2 30min-1
        self.df[self.nep_col] = self.df[self.nep_col].multiply(0.0792171)

        # Columns that will be limited
        self.vpd_col_limited = f'_LIMITED_{self.vpd_col}'
        # VPD limited potentially needs gapfilling b/c it is calculated from TA limited
        self.vpd_col_limited_gapfilled = f'{self.vpd_col_limited}_gfRF'
        self.ta_col_limited = f'_LIMITED_{self.ta_col}'
        self.swin_col_limited = f'_LIMITED_{self.swin_col}'
        self.swin_col_limited_gapfilled = f'_LIMITED_{self.swin_col}_gfRF'
        self.nep_col_limited = f'_LIMITED_{self.nep_col}'
        self.nep_col_limited_gf = f'_LIMITED_{self.nep_col}_gfRF'

        # Results from gapfilling
        self._gapfilled_df = None
        self._gf_results = None
        self._penalty_hires_df = None
        self._penalty_min_year = None

    @property
    def penalty_hires_df(self) -> DataFrame:
        """Return gap-filled dataframe"""
        if not isinstance(self._penalty_hires_df, DataFrame):
            raise Exception('No gap-filled data found')
        return self._penalty_hires_df

    @property
    def penalty_per_year_df(self) -> DataFrame:
        """Yearly overview of carbon cost per year"""
        if not isinstance(self._penalty_per_year_df, DataFrame):
            raise Exception('No gap-filled data found')
        return self._penalty_per_year_df

    @property
    def penalty_min_year(self) -> int:
        """Year when carbon cost was highest (=most negative number, minimum)"""
        if not isinstance(self._penalty_min_year, int):
            raise Exception('No gap-filled data found')
        return self._penalty_min_year

    @property
    def gapfilled_df(self) -> DataFrame:
        """Return gap-filled dataframe for focus months"""
        if not isinstance(self._gapfilled_df, DataFrame):
            raise Exception('No gap-filled data found')
        return self._gapfilled_df

    @property
    def gf_results(self) -> DataFrame:
        """Return gap-filled data results for focus months"""
        if not self._gf_results:
            raise Exception('No gap-filled data found')
        return self._gf_results

    def calculate_penalty(self, **kwargs):
        self._penalty_hires_df, self._penalty_per_year_df, self._gapfilled_df, self._gf_results, self._penalty_min_year = \
            self._calculate_penalty(**kwargs)

    def _gapfill(self, df: DataFrame, target_col: str, random_state: int = None, n_bootstrap_runs: int = 11,
                 lagged_variants: int = 1):
        # Gapfilling random forest
        rfts = RandomForestTS(df=df,
                              target_col=target_col,
                              include_timestamp_as_features=True,
                              lagged_variants=lagged_variants,
                              use_neighbor_years=True,
                              feature_reduction=False,
                              verbose=1)
        rfts.build_models(n_estimators=n_bootstrap_runs,
                          random_state=random_state,
                          min_samples_split=2,
                          min_samples_leaf=1,
                          n_jobs=-1)
        rfts.gapfill_yrs()
        _gapfilled_df, _gf_results = rfts.get_gapfilled_dataset()

        # Reindex to have same index as full dataset (full timestamp range)
        _gapfilled_df = _gapfilled_df.reindex(self.df.index)
        return _gapfilled_df, _gf_results

    def _calculate_penalty(self, random_state: int = None):

        print("Calculating penalty ...")

        # Limit/remove CRD data
        _df = self._limit_crd_data()

        # Make subset with vars required for gapfilling
        # limited_cols = [col for col in _df.columns if '_LIMITED_' in col]
        limited_cols = [self.nep_col_limited, self.ta_col_limited,
                        self.vpd_col_limited_gapfilled, self.swin_col_limited_gapfilled]
        _df_limited = _df[limited_cols].copy()

        # Gapfilling
        gapfilled_df, gf_results = self._gapfill(df=_df_limited,
                                                 target_col=self.nep_col_limited,
                                                 random_state=random_state,
                                                 **self.random_forest_params)

        # Merge gapfilled with full data range
        penalty_df = self.df.copy()

        gapfilled_flag_col = f'QCF_{self.nep_col_limited_gf}'
        penalty_df[self.nep_col_limited_gf] = gapfilled_df[self.nep_col_limited_gf].copy()
        penalty_df[gapfilled_flag_col] = gapfilled_df[gapfilled_flag_col].copy()

        # Calculate carbon cost
        penalty_df['PENALTY'] = penalty_df[self.nep_col_limited_gf].sub(penalty_df[self.nep_col])

        # Cumulatives
        penalty_df[f'CUMSUM_{self.nep_col_limited_gf}'] = penalty_df[self.nep_col_limited_gf].cumsum()
        penalty_df[f'CUMSUM_{self.nep_col}'] = penalty_df[self.nep_col].cumsum()
        penalty_df['CUMSUM_PENALTY'] = penalty_df['PENALTY'].cumsum()

        # Add limited columns
        penalty_df[limited_cols] = _df_limited[limited_cols].copy()

        # Add flag columns
        penalty_df['FLAG_CRD'] = _df['FLAG_CRD'].copy()
        penalty_df['FLAG_nCRD'] = _df['FLAG_nCRD'].copy()

        # Collect info for yearly overview

        # Detect year with highest carbon cost
        penalty_per_year_df = penalty_df[['PENALTY']].groupby(penalty_df.index.year).sum()
        penalty_per_year_df[self.nep_col_limited_gf] = \
            penalty_df[self.nep_col_limited_gf].groupby(penalty_df.index.year).sum()
        penalty_per_year_df[f'{self.nep_col}'] = penalty_df[self.nep_col].groupby(penalty_df.index.year).sum()

        # Add info about number of CRDs
        _num_crds = penalty_df['VPD_f'].resample('D').max()
        _num_crds = _num_crds.loc[_num_crds > self.thres_crd]
        _num_crds = _num_crds.groupby(_num_crds.index.year).count()
        _num_crds = _num_crds.fillna(0)
        penalty_per_year_df['num_CRDs'] = _num_crds

        penalty_min_year = int(penalty_per_year_df['PENALTY'].idxmin())
        penalty_min = penalty_per_year_df.min()

        return penalty_df, penalty_per_year_df, gapfilled_df, gf_results, penalty_min_year

    def _limit_crd_data(self) -> DataFrame:
        """Limit/remove data on critical heat days

        - Set CRD data to their diel cycle medians
        - Remove NEP CRD data
        """

        # Insert aggregated x as column in hires dataframe
        df, aggcol = frames.insert_aggregated_in_hires(df=self.df.copy(), col=self.vpd_col,
                                                       to_freq='D', to_agg='max', agg_offset='7H')

        # Get hires TA for nCRDs
        ncrds_ix = (df[aggcol] >= self.thres_ncrd_lower) \
                   & (df[aggcol] < self.thres_crd) \
                   & (df.index.month >= self.penalty_start_month) \
                   & (df.index.month <= self.penalty_end_month)
        ncrds_ta_hires_df = df.loc[ncrds_ix, self.ta_col].copy()
        # ncrds_ta_hires_df = df.loc[ncrds_ix, self.features_cols].copy()

        # Calculate TA diel cycles (half-hourly medians) for nCRDs
        diel_cycles_ncrds_df = self._diel_cycle(df=ncrds_ta_hires_df, agg='median')

        # Time as column for merging
        diel_cycles_ncrds_df['TIME'] = diel_cycles_ncrds_df.index
        diel_cycles_ncrds_df.index.name = 'INDEX'  # Rename to avoid same name as TIME column, otherwise merging fails
        df['TIME'] = df.index.time

        # Add TA diel cycles from nCRDs to full data, merge on time
        orig_ix_name = df.index.name
        df[orig_ix_name] = df.index  # Original index
        df = df.merge(diel_cycles_ncrds_df, left_on='TIME', right_on='TIME', how='left')
        df = df.set_index(orig_ix_name)  # Re-apply original index (merging lost index)

        # Indices of CRD data
        crds_ix = (df[aggcol] >= self.thres_crd) \
                  & (df.index.month >= self.penalty_start_month) \
                  & (df.index.month <= self.penalty_end_month)

        # Remove TA CRD data and replace with TEMPLATE (diel cycles)
        # TA CRD data will be replaced with nCRD diel cycle median
        matching_template_col = f'_TEMPLATE_{self.ta_col}'
        df[self.ta_col_limited] = df[self.ta_col].copy()  # Copy original data
        df.loc[crds_ix, self.ta_col_limited] = np.nan  # Remove data on critical days, creates gaps
        df[self.ta_col_limited].fillna(df[matching_template_col],
                                       inplace=True)  # Fill gaps with diel cycle nCRD medians

        # Calculate VPD from limited TA and original (measured) RH,
        # also needs gap-filling (using SW_IN, limited TA and timestamp info as features)
        df[self.vpd_col_limited] = calc_vpd_from_ta_rh(df=df, rh_col=self.rh_col, ta_col=self.ta_col_limited)
        rf_subset = df[[self.vpd_col_limited, self.swin_col, self.ta_col_limited]].copy()
        _gapfilled_df, _gf_results = self._gapfill(df=rf_subset, target_col=self.vpd_col_limited,
                                                   **self.random_forest_params)
        df[self.vpd_col_limited_gapfilled] = _gapfilled_df[self.vpd_col_limited_gapfilled].copy()

        # Remove SW_IN CRD data
        # SW_IN CRD data will be replaced with random forest gapfilling
        # matching_template_col = f'_TEMPLATE_{self.swin_col}'
        df[self.swin_col_limited] = df[self.swin_col].copy()  # Copy original data
        df.loc[crds_ix, self.swin_col_limited] = np.nan  # Remove data on critical days, creates gaps
        rf_subset = df[[self.swin_col_limited, self.ta_col_limited]].copy()
        _gapfilled_df, _gf_results = self._gapfill(df=rf_subset, target_col=self.swin_col_limited,
                                                   lagged_variants=0,
                                                   **self.random_forest_params)
        df[self.swin_col_limited_gapfilled] = _gapfilled_df[self.swin_col_limited_gapfilled].copy()

        # Remove NEP on critical days
        # Will be gap-filled with random forest
        df[self.nep_col_limited] = df[self.nep_col].copy()
        df.loc[crds_ix, self.nep_col_limited] = np.nan

        # Add flag to mark CRD and nCRD data
        flag_crd_col = 'FLAG_CRD'
        df[flag_crd_col] = 0
        df.loc[crds_ix, flag_crd_col] = 1

        flag_ncrd_col = 'FLAG_nCRD'
        df[flag_ncrd_col] = 0
        df.loc[ncrds_ix, flag_ncrd_col] = 1

        # # Plot check
        # import matplotlib.pyplot as plt
        # df[['Tair_f', '_TEMPLATE_Tair_f', '_LIMITED_Tair_f']].plot(title='Tair_f', xlim=('2019-06-15', '2019-07-15'), subplots=True)
        # df[['VPD_f', '_TEMPLATE_VPD_f', '_LIMITED_VPD_f']].plot(title='VPD_f', xlim=('2019-06-15', '2019-07-15'), subplots=True)
        # df[['NEP', '_LIMITED_NEP_f']].plot(title=nep_col, xlim=('2019-06-15', '2019-07-15'))
        # plt.show()

        return df

    def _diel_cycle(self, df: DataFrame, agg: str or dict) -> DataFrame:
        """Calculate diel cycles grouped by time"""
        diel_cycles_df = DataFrame(df)
        diel_cycles_df['TIME'] = diel_cycles_df.index.time
        diel_cycles_df = diel_cycles_df.groupby('TIME').agg(agg)
        diel_cycles_df = diel_cycles_df.add_prefix('_TEMPLATE_')
        return diel_cycles_df

    def plot_critical_hours(self, ax,
                            which_threshold: Literal['crd'] = 'crd',
                            figletter: str = '',
                            show_fitline: bool = True,
                            fit_n_bootstraps: int = 10,
                            show_year_labels: bool = False,
                            show_title: bool = False,
                            fit_type: str = 'quadratic',
                            labels_below: list = None,
                            labels_above: list = None,
                            labels_shifted: list = None,
                            decorate_labels1: list = None,
                            decorate_labels2: list = None):

        df = self.df.copy()

        if fit_n_bootstraps < 2: fit_n_bootstraps = 2

        xlabel_units = "$\mathrm{hours\ yr^{-1}}$"
        ylabel_penalty = r"$\mathrm{NEP\ penalty}$"
        ylabel_units = r"$\mathrm{gCO_{2}\ m^{-2}\ yr^{-1}}$"

        # Count half-hours > CRD threshold
        # thr_crd = self.results_crd_threshold_detection['thres_crd']
        locs_above_thr_crd = df[self.vpd_col] > self.thres_crd
        df_above_thr_crd = df.loc[locs_above_thr_crd, self.vpd_col].copy()
        hh_above_thr_crd = df_above_thr_crd.groupby(df_above_thr_crd.index.year).count()

        # # Count half-hours > xCRD threshold
        # locs_above_thr_xcrd = df[self.vpd_col] > self.thres_xcrd
        # df_above_thr_xcrd = df.loc[locs_above_thr_xcrd, self.vpd_col].copy()
        # hh_above_thr_xcrd = df_above_thr_xcrd.groupby(df_above_thr_xcrd.index.year).count()

        # Penalty YY results, overview
        penalty_per_year = self.penalty_per_year_df['PENALTY'].copy()
        # penalty_per_year = penalty_per_year.multiply(-1)  # Make penalty positive --> is already positive w/ NEP
        # penalty_per_year.loc[penalty_per_year < 0] = 0  # Leave negative CCT in dataset
        penalty_per_year.loc[penalty_per_year == 0] = 0

        # Combine
        penalty_vs_hh_thr = pd.DataFrame(penalty_per_year)
        penalty_vs_hh_thr['Hours above THR_CRD'] = hh_above_thr_crd.divide(2)
        # penalty_vs_hh_thr['Hours above THR_xCRD'] = hh_above_thr_xcrd.divide(2)
        penalty_vs_hh_thr = penalty_vs_hh_thr.fillna(0)

        xcol = 'Hours above THR_CRD'
        if which_threshold == 'crd':
            xcol = 'Hours above THR_CRD'
        # elif which_threshold == 'xcrd':
        #     xcol = 'Hours above THR_xCRD'

        # Fit
        if show_fitline:

            # Bootstrapping, mainly for prediction intervals 95% range
            bts_fit_results = {}
            predict_min_x = penalty_vs_hh_thr[xcol].min()  # Prediction range, same for all bootstraps
            predict_max_x = penalty_vs_hh_thr[xcol].max()

            n_bts_successful = 0  # Number of succesful bootstrapping runs
            while n_bts_successful < fit_n_bootstraps:
                # for bts_run in range(0, fit_n_bootstraps):

                try:
                    if n_bts_successful == 0:
                        # Run zero is the original data, not bootstrapped
                        bts_df = penalty_vs_hh_thr.copy()
                    else:
                        # Bootstrap data
                        bts_df = penalty_vs_hh_thr.sample(n=int(len(penalty_vs_hh_thr)), replace=True,
                                                          random_state=None)

                    # Fit
                    fitter = BinFitterCP(df=bts_df,
                                         x_col=xcol,
                                         y_col='PENALTY',
                                         num_predictions=10000,
                                         predict_min_x=predict_min_x,
                                         predict_max_x=predict_max_x,
                                         bins_x_num=0,
                                         bins_y_agg='mean',
                                         fit_type=fit_type)
                    fitter.run()
                    bts_fit_results[n_bts_successful] = fitter.get_results()
                    n_bts_successful += 1
                    print(f"Bootstrapping run {n_bts_successful} for fit line successful ... ")
                except:
                    print(f"Bootstrapping for fit line failed, repeating run {n_bts_successful + 1} ... ")
                    pass

            # Collect bootstrapping results
            _fit_x_predbands = pd.DataFrame()
            _upper_predbands = pd.DataFrame()
            _lower_predbands = pd.DataFrame()
            for bts_run in range(1, fit_n_bootstraps):
                _fit_x_predbands[bts_run] = bts_fit_results[bts_run]['fit_df']['fit_x'].copy()
                _upper_predbands[bts_run] = bts_fit_results[bts_run]['fit_df']['upper_predband'].copy()
                _lower_predbands[bts_run] = bts_fit_results[bts_run]['fit_df']['lower_predband'].copy()
            predbands_quantiles_95 = pd.DataFrame()
            predbands_quantiles_95['fit_x'] = _fit_x_predbands.mean(axis=1)  # Output is the same for all bootstraps
            predbands_quantiles_95['upper_Q97.5'] = _upper_predbands.quantile(q=.975, axis=1)
            predbands_quantiles_95['lower_Q97.5'] = _lower_predbands.quantile(q=.975, axis=1)
            predbands_quantiles_95['upper_Q02.5'] = _upper_predbands.quantile(q=.025, axis=1)
            predbands_quantiles_95['lower_Q02.5'] = _lower_predbands.quantile(q=.025, axis=1)

            # Fitplot
            line_xy_gpp, line_fit_gpp, line_fit_ci_gpp, line_fit_pb_gpp, line_highlight = \
                fitplot(ax=ax,
                        label='year',
                        flux_bts_results=bts_fit_results[0],
                        alpha=1,
                        edgecolor=COLOR_RECO,
                        color=COLOR_RECO,
                        color_fitline=COLOR_RECO,
                        show_prediction_interval=False,
                        size_scatter=90,
                        fit_type=fit_type)

            # line_xy = ax.plot(bts_fit_results[0]['fit_df']['fit_x'],
            #                      bts_fit_results[0]['fit_df']['upper_predband'],
            #                      zorder=1, ls='--', color=COLOR_RECO, label="95% prediction interval")
            # line_xy = ax.plot(bts_fit_results[0]['fit_df']['fit_x'],
            #                      bts_fit_results[0]['fit_df']['lower_predband'],
            #                      zorder=1, ls='--', color=COLOR_RECO)

            # ax.fill_between(predbands_quantiles_95['fit_x'],
            #                 predbands_quantiles_95['upper_Q97.5'],
            #                 predbands_quantiles_95['upper_Q02.5'],
            #                 alpha=.2, lw=0, color='#ef9a9a', edgecolor='white',
            #                 zorder=1)
            # ax.fill_between(predbands_quantiles_95['fit_x'],
            #                 predbands_quantiles_95['lower_Q97.5'],
            #                 predbands_quantiles_95['lower_Q02.5'],
            #                 alpha=.2, lw=0, color='#ef9a9a', edgecolor='white',
            #                 zorder=1)

            # Prediction bands (smoothed)
            line_xy = ax.plot(predbands_quantiles_95['fit_x'],
                              predbands_quantiles_95['upper_Q97.5'].rolling(400, center=True).mean(),
                              zorder=1, ls='--', color=COLOR_RECO, label="95% prediction interval")
            line_xy = ax.plot(predbands_quantiles_95['fit_x'],
                              predbands_quantiles_95['lower_Q02.5'].rolling(400, center=True).mean(),
                              zorder=1, ls='--', color=COLOR_RECO)

        # Title
        if show_title:
            ax.set_title(f"{figletter} {ylabel_penalty} per year", x=0.05, y=1, size=24, ha='left', va='top',
                         weight='normal')
        # ax.set_title(f"Carbon cost per year", x=0.95, y=0.95, size=24, ha='right', weight='bold')
        # ax.text(0.95, 0.93, f"{_units}", size=20, color='#9E9E9E', backgroundcolor='none', alpha=1,
        #         horizontalalignment='right', verticalalignment='center', transform=ax.transAxes, weight='bold')

        # Format

        default_format(ax=ax,
                       txt_xlabel=f'Hours above VPD threshold ({xlabel_units})',
                       txt_ylabel=f'{ylabel_penalty} ({ylabel_units})',
                       showgrid=False)

        # Legend
        default_legend(ax=ax, ncol=1, loc=(.07, .75))
        # ax.legend(ncol=1, edgecolor='none', loc=(.1, .7), prop={'size': 16}, facecolor='none')

        # Spines
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)

        if show_year_labels:

            for year in penalty_vs_hh_thr.index:
                txt_y = penalty_vs_hh_thr['PENALTY'].loc[penalty_vs_hh_thr.index == year].values[0]
                txt_x = penalty_vs_hh_thr[xcol].loc[penalty_vs_hh_thr.index == year].values[0]
                xytext = (-14, -18)
                arrowprops = None

                if year in labels_below:
                    pass
                elif year in labels_above:
                    xytext = (-14, 10)
                elif year in labels_shifted:
                    if year == 2018:
                        xytext = (-14, -40)
                    elif year == 2020:
                        xytext = (-60, -5)
                    elif year == 2006:
                        xytext = (20, -15)
                    elif year == 2015:
                        xytext = (-20, -40)
                    arrowprops = dict(facecolor='black', arrowstyle='-')
                else:
                    xytext = None

                decoration = ""
                if year in decorate_labels1:
                    decoration = "***"
                elif year in decorate_labels2:
                    decoration = "*"

                if xytext:
                    text_in_ax = \
                        ax.annotate(f'{year}{decoration}', (txt_x, txt_y),
                                    xytext=xytext, xycoords='data',
                                    color='black', weight='normal', fontsize=10,
                                    textcoords='offset points', zorder=100,
                                    arrowprops=arrowprops)

    def plot_cumulatives(self, ax,
                         figletter: str = '',
                         year: int = None,
                         showline_modeled: bool = True,
                         showfill_penalty: bool = True,
                         showtitle: bool = True):

        gapfilled_col = f'_LIMITED_{self.nep_col}_gfRF'
        label_penalty = r"$\mathrm{NEP\ penalty}$"
        label_units_cumulative = r"$\mathrm{gCO_{2}\ m^{-2}}$"

        penalty_perc = None
        if year:
            df = self.penalty_hires_df.loc[self.penalty_hires_df.index.year == year]
            num_crds = int(self.penalty_per_year_df.loc[self.penalty_per_year_df.index == year]['num_CRDs'])
            obs = float(self.penalty_per_year_df.loc[self.penalty_per_year_df.index == year][self.nep_col])
            pot = float(self.penalty_per_year_df.loc[self.penalty_per_year_df.index == year][gapfilled_col])
            penalty = float(self.penalty_per_year_df.loc[self.penalty_per_year_df.index == year]['PENALTY'])

            # obs and pot both show UPTAKE and
            # pot shows *MORE* uptake than obs
            if (obs > 0) and (pot > 0) and (pot > obs):
                diff = pot - obs
                penalty_perc = diff / pot
                penalty_perc *= 100
                # Example:
                #   pot=488, obs=378
                #   488 - 378 = 110
                #   110 / 488 = 0.225
                #   obs uptake was 22.5% lower compared to pot

            # obs and pot both show EMISSION and
            # pot shows *LESS* emission than obs
            elif (obs < 0) and (pot < 0) and (pot < obs):
                diff = pot - obs
                penalty_perc = diff / abs(pot)
                penalty_perc *= 100
                # Example:
                #   pot=-115, obs=-150
                #   -115 - -150 = 35
                #   35 / 115 = 0.304
                #   obs emission was 30.4% higher compared to pot

            else:
                penalty_perc = -9999


        else:
            df = self.penalty_hires_df
            num_crds = int(self.penalty_per_year_df['num_CRDs'].sum())
            obs = float(self.penalty_per_year_df[self.nep_col].sum())
            pot = float(self.penalty_per_year_df[gapfilled_col].sum())
            penalty = float(self.penalty_per_year_df['PENALTY'].sum())
            if (obs < 0) and (pot < 0) and (pot < obs):
                penalty_perc = (1 - (obs / pot)) * 100

        cumulative_orig = df[self.nep_col].cumsum()  # Cumulative of original measured and gap-filled NEP
        cumulative_model = df[gapfilled_col].cumsum()  # NEP where hot days were modeled

        # Original data as measured and gap-filled
        x = cumulative_orig.index
        y = cumulative_orig
        ax.plot_date(x=x, y=y, color=COLOR_NEP, alpha=0.9, ls='-', lw=3, marker='',
                     markeredgecolor='none', ms=0, zorder=99, label='observed NEP')
        ax.plot_date(x[-1], y[-1], ms=10, zorder=100, color=COLOR_NEP)
        ax.text(x[-1], y[-1], f"    {cumulative_orig[-1]:.0f}", size=20,
                color=COLOR_NEP, backgroundcolor='none', alpha=1,
                horizontalalignment='left', verticalalignment='center')

        # Modeled hot days
        if showline_modeled:
            linestyle = '-'
            marksersize = 10
            txtsize = 20
            label = 'potential NEP'
        else:
            # Hide elements if not required
            linestyle = 'None'
            marksersize = 0
            txtsize = 0
            label = None
        x = cumulative_model.index
        y = cumulative_model
        ax.plot_date(x=x, y=y, color=COLOR_RECO, alpha=0.9, ls=linestyle, lw=3, marker='',
                     markeredgecolor='none', ms=0, zorder=98, label=label)
        ax.plot_date(x[-1], y[-1], ms=marksersize, zorder=100, color=COLOR_RECO)
        ax.text(x[-1], y[-1], f"    {cumulative_model[-1]:.0f}", size=txtsize, color=COLOR_RECO,
                backgroundcolor='none', alpha=1, horizontalalignment='left',
                verticalalignment='center')

        # Fill between: NEP penalty
        if showfill_penalty:
            mpl.rcParams['hatch.linewidth'] = 2  # Set width of hatch lines
            ax.fill_between(cumulative_model.index, cumulative_model, cumulative_orig,
                            alpha=.7, lw=0, color='#ef9a9a', edgecolor='white',
                            zorder=1, hatch='//', label=label_penalty)
            txt = f"critical days: {num_crds}\n" \
                  f"NEP reduction: {penalty_perc:.0f}%\n" \
                  f"{label_penalty}: {np.abs(penalty):.0f} {label_units_cumulative}\n"
            # r"$\bf{" + str(number) + "}$"
            ax.text(.5, .1, txt, size=16, color='black', backgroundcolor='none',
                    alpha=1, horizontalalignment='left', verticalalignment='center',
                    transform=ax.transAxes, weight='normal', linespacing=1.4)

        # Zero-line
        ax.axhline(0, color='black')

        # Title
        if showtitle:
            if year:
                title_year = year
            else:
                title_year = f"{df.index.year[0]} - {df.index.year[-1]}"
            ax.set_title(f"{figletter} {label_penalty} {title_year}", x=0.05, y=1, size=24, ha='left', weight='normal')

        # Format
        default_format(ax=ax,
                       txt_xlabel='Date',
                       txt_ylabel=f"Cumulative NEP ({label_units_cumulative})",
                       showgrid=False)

        # Legend
        default_legend(ax=ax, ncol=1, loc=(.11, .82))
        # ax.legend(ncol=1, edgecolor='none', loc=(.5, .7), prop={'size': 14})

        # Ticks
        ax.tick_params(axis='both', which='major', direction='in', labelsize=16, length=8, size=5)  # x/y ticks text

        # Nice format for date ticks
        locator = mdates.AutoDateLocator(minticks=12, maxticks=12)
        ax.xaxis.set_major_locator(locator)
        formatter = mdates.ConciseDateFormatter(locator, show_offset=False)
        ax.xaxis.set_major_formatter(formatter)

        # Limits
        # ax.set_xlim(df.index[0], df.index[-1])

        # Spines
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)

    def plot_day_example(self, ax_nep, ax_ta, ax_vpd, ax_swin,
                         showline_nep_modeled: bool = True,
                         showline_ta_modeled: bool = True,
                         showline_vpd_modeled: bool = True,
                         showline_swin_modeled: bool = True,
                         showfill_penalty: bool = True,
                         ):

        label_penalty = r"$\mathrm{NEP\ penalty}$"
        label_units = r"$\mathrm{gCO_{2}\ m^{-2}\ 30min^{-1}}$"

        df = self.penalty_hires_df.copy()

        subset = df.loc[df['FLAG_CRD'] == 1, :].copy()
        subset = subset.groupby(subset.index.time).mean()
        subset.index = pd.to_datetime(subset.index, format='%H:%M:%S')

        x = subset.index
        props = dict(ls='-')

        # Observed NEP
        ax_nep.plot_date(x=x, y=subset[self.nep_col],
                         label="on critical days", color=COLOR_NEP, lw=2, ms=6, **props)

        # Modeled NEP
        lw = 2 if showline_nep_modeled else 0
        ms = 6 if showline_nep_modeled else 0
        label = "modeled" if showline_nep_modeled else None
        ax_nep.plot_date(x=x, y=subset[self.nep_col_limited_gf],
                         label=label, color=COLOR_RECO, lw=lw, ms=ms, **props)

        # Observed TA
        ax_ta.plot_date(x=x, y=subset[self.ta_col],
                        label="on critical days", color=COLOR_NEP, lw=2, ms=6, **props)

        # Modeled TA
        lw = 2 if showline_ta_modeled else 0
        ms = 6 if showline_ta_modeled else 0
        label = "on near-critical days" if showline_ta_modeled else None
        ax_ta.plot_date(x=x, y=subset[self.ta_col_limited],
                        label=label, color=COLOR_RECO, lw=lw, ms=ms, **props)

        # Observed VPD
        ax_vpd.plot_date(x=x, y=subset[self.vpd_col],
                         label="on critical days", color=COLOR_NEP, lw=2, ms=6, **props)

        # Newly calculated VPD
        lw = 2 if showline_vpd_modeled else 0
        ms = 6 if showline_vpd_modeled else 0
        label = "newly calculated" if showline_vpd_modeled else None
        ax_vpd.plot_date(x=x, y=subset[self.vpd_col_limited_gapfilled], color=COLOR_RECO,
                         label=label, lw=lw, ms=ms, **props)

        # Observed SW_IN
        ax_swin.plot_date(x=x, y=subset[self.swin_col],
                          label="on critical days", color=COLOR_NEP, lw=2, ms=6, **props)

        # SW_IN from random forest
        lw = 2 if showline_swin_modeled else 0
        ms = 6 if showline_swin_modeled else 0
        label = "modeled" if showline_swin_modeled else None
        ax_swin.plot_date(x=x, y=subset[self.swin_col_limited_gapfilled], color=COLOR_RECO,
                          label=label, lw=lw, ms=ms, **props)

        # Fill area penalty
        if showfill_penalty:
            ax_nep.fill_between(x, subset[self.nep_col], subset[self.nep_col_limited_gf],
                                alpha=.7, lw=0, color='#ef9a9a', edgecolor='white',
                                zorder=1, hatch='//', label=label_penalty)

        props = dict(x=.5, y=1.05, size=24, ha='center', va='center', weight='normal')
        ax_nep.set_title("NEP", **props)
        ax_ta.set_title("TA", **props)
        ax_vpd.set_title("VPD", **props)
        ax_swin.set_title("SW_IN", **props)

        ax_nep.axhline(0, color='black', lw=1)

        default_legend(ax=ax_nep, loc='upper center')
        default_legend(ax=ax_ta, loc='upper left')
        default_legend(ax=ax_vpd, loc='upper left')
        default_legend(ax=ax_swin, loc='upper left')

        props = dict(axlabels_fontsize=16)
        default_format(ax=ax_nep, txt_ylabel='NEP', txt_ylabel_units=f"({label_units})", **props)
        default_format(ax=ax_ta, txt_ylabel='TA', txt_ylabel_units="(°C)", txt_xlabel='Time (hour)', **props)
        default_format(ax=ax_vpd, txt_ylabel="VPD", txt_ylabel_units="(kPa)", **props)
        default_format(ax=ax_swin, txt_ylabel="SW_IN", txt_ylabel_units="(W m-2)", **props)

        nice_date_ticks(ax=ax_nep, locator='hour')
        nice_date_ticks(ax=ax_ta, locator='hour')
        nice_date_ticks(ax=ax_vpd, locator='hour')
        nice_date_ticks(ax=ax_swin, locator='hour')

    def showplot_critical_hours(self,
                                saveplot: bool = True,
                                title: str = None,
                                path: Path or str = None,
                                dpi: int = 72,
                                **kwargs):
        fig = plt.figure(facecolor='white', figsize=(9, 9), dpi=dpi)
        gs = gridspec.GridSpec(1, 1)  # rows, cols
        # gs.update(wspace=0, hspace=0, left=.2, right=.8, top=.8, bottom=.2)
        ax = fig.add_subplot(gs[0, 0])
        self.plot_critical_hours(ax=ax, **kwargs)
        fig.tight_layout()
        fig.show()
        if saveplot:
            save_fig(fig=fig, title=title, path=path)

    def showplot_day_example(self,
                             saveplot: bool = False,
                             title: str = None,
                             path: Path or str = None,
                             dpi: int = 72,
                             **kwargs):
        fig = plt.figure(facecolor='white', figsize=(23, 6), dpi=dpi)
        gs = gridspec.GridSpec(1, 4)  # rows, cols
        # gs.update(wspace=0, hspace=0, left=.2, right=.8, top=.8, bottom=.2)
        ax_nep = fig.add_subplot(gs[0, 0])
        ax_ta = fig.add_subplot(gs[0, 1])
        ax_vpd = fig.add_subplot(gs[0, 2])
        ax_swin = fig.add_subplot(gs[0, 3])
        self.plot_day_example(ax_nep=ax_nep, ax_ta=ax_ta,
                              ax_vpd=ax_vpd, ax_swin=ax_swin, **kwargs)
        fig.tight_layout()
        fig.show()
        if saveplot:
            save_fig(fig=fig, title=title, path=path)

    def showplot_cumulatives(self,
                             saveplot: bool = False,
                             title: str = '',
                             path: Path or str = None,
                             dpi: int = 72,
                             **kwargs):
        fig = plt.figure(facecolor='white', figsize=(9, 9), dpi=dpi)
        gs = gridspec.GridSpec(1, 1)  # rows, cols
        # gs.update(wspace=0, hspace=0, left=.2, right=.8, top=.8, bottom=.2)
        ax = fig.add_subplot(gs[0, 0])
        self.plot_cumulatives(ax=ax, **kwargs)
        fig.tight_layout()
        fig.show()
        if saveplot:
            save_fig(fig=fig, title=title, path=path)


if __name__ == '__main__':
    pass
