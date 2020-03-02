"""The :mod:`sklearn.robust_lda` module implements a parameter selection based on stability of multiple runs.
"""
# Author: Christoph Kralj <christoph.kralj@gmail.com>
#
# License: MIT

import math
import numpy as np
from sklearn.decomposition import LatentDirichletAllocation, NMF
from scipy.spatial.distance import pdist
from scipy.spatial.distance import jensenshannon
from scipy.stats import kendalltau, spearmanr, wasserstein_distance
import sobol_seq

"""
Sources who show how to use Topic models

https://medium.com/mlreview/topic-modeling-with-scikit-learn-e80d33668730


TEST CODE

from sklearn.datasets import fetch_20newsgroups

dataset = fetch_20newsgroups(
    shuffle=True, random_state=1, remove=('headers', 'footers', 'quotes'))
documents = dataset.data[:100]

from sklearn.feature_extraction.text import TfidfVectorizer, CountVectorizer

no_features = 1000

# NMF is able to use tf-idf
tfidf_vectorizer = TfidfVectorizer(
    max_df=0.95, min_df=2, max_features=no_features, stop_words='english')
tfidf = tfidf_vectorizer.fit_transform(documents)
tfidf_feature_names = tfidf_vectorizer.get_feature_names()

# LDA can only use raw term counts for LDA because it is a probabilistic graphical model
tf_vectorizer = CountVectorizer(
    max_df=0.95, min_df=2, max_features=no_features, stop_words='english')
tf = tf_vectorizer.fit_transform(documents)
tf_feature_names = tf_vectorizer.get_feature_names()

# Initialize and fit the data
topics = RobustTopics(n_iterations=4)
topics.load_sklearn_lda_data(tf, 5)
topics.load_sklearn_nmf_data(tfidf, 5, setup="complex")
topics.fit_models()

# Compare different samples
# topics.stability_report
topics.rank_models("mean")

# Look at topics for a specific model
topics.analyse_sample("sklearn_nmf", 0, tfidf_feature_names)

# Convert the stability report to a pandas dataframe
pd.DataFrame.from_records(topics.stability_report)

# Print histograms
import plotly.express as px
def show_stability_histograms(self):
    for sample in self.rank_models():
        fig = px.histogram(data_frame=sample["mean"], x=0, nbins=10, range_x=[
                        0, 1], title="Model: "+sample["model"]+" Topics: "+str(sample["n_topics"]) + " Mean: "+str(sample["mean/overall"]))
        fig.show()

"""


class RobustTopics():
    """Run different topic models multiple times and return them by their ranked by topic stability.

    Some Explanation
    ----------
    n_components : [min_n, max_n], default=[1, 20]
        Minimum and maximum values for the n_components parameter.
    n_samples : int, default=10
        The number of samples taken from the n_components range.
    n_iterations : int, default=20
        The number of random runs each sample is computed.
        These are used to compute the robustness.
    models : [sklearn topic model classes], default=[LatentDirichletAllocation]
        Possibilities: LatentDirichletAllocation, NMF

    See also
    --------
    sklearn.decomposition.LatentDirichletAllocation : LDA implementation.
    sklearn.decomposition.NMF : NMF implementation.
    """

    def __init__(self, n_iterations=10, n_relevant_top_words=20):
        self.n_iterations = n_iterations
        self.n_relevant_top_words = n_relevant_top_words

        self.models = {}

    def fit_models(self):
        """Fit the models

        Parameters
        ----------
        X : array-like or sparse matrix, shape=(n_samples, n_features)
        y : Ignored

        Returns
        -------
        self : object
            Returns self.
        """

        for name, settings in self.models.items():
            print("Model: " + name)

            for sample in settings["sampling"]:
                model_iterations = []
                print(sample)

                for it in range(1, self.n_iterations+1):
                    print("Iteration: "+str(it)+"/"+str(self.n_iterations))

                    if name == "sklearn_lda":
                        model_iterations.append(
                            (LatentDirichletAllocation(**sample).fit(settings["data"])))

                    if name == "sklearn_nmf":
                        model_iterations.append(
                            (NMF(**sample).fit(settings["data"])))

                settings["samples"].append(model_iterations)

        self._compute_topic_stability()

        return self

    def load_sklearn_lda_data(self, X, n_samples, setup="simple", custom_params=None):
        model_informations = {
            "n_samples": n_samples,
            "data": X,
            "samples": [],
            "topic_terms": [],
            "report": [],
            "report_full": []
        }

        if setup == "simple":
            model_informations["params"] = {
                "n_components":
                {"type": int, "mode": "range", "values": [5, 50]}
            }

        if setup == "complex":
            model_informations["params"] = {
                "n_components":
                {"type": int, "mode": "range", "values": [5, 50]},
                "learning_decayfloat":
                {"type": float, "mode": "range", "values": [0.51, 1]}
            }

        if setup == "custom":
            model_informations["params"] = custom_params

        model_informations["sampling"] = self._compute_param_combinations(
            model_informations["params"], n_samples)

        self.models["sklearn_lda"] = model_informations

    def load_sklearn_nmf_data(self, X, n_samples, setup="simple", custom_params=None):
        model_informations = {
            "n_samples": n_samples,
            "data": X,
            "samples": [],
            "topic_terms": [],
            "report": [],
            "report_full": []
        }

        if setup == "simple":
            model_informations["params"] = {
                "n_components":
                {"type": int, "mode": "range", "values": [5, 50]},
                "init":
                {"type": str, "mode": "fixed", "values": "random"},
            }

        if setup == "complex":
            model_informations["params"] = {
                "n_components":
                {"type": int, "mode": "range", "values": [5, 50]},
                "init":
                {"type": str, "mode": "list", "values": [
                    "random", "nndsvd", "nndsvda", None]},
                "solver":
                {"type": str, "mode": "fixed", "values": "mu"},
                "beta_loss":
                {"type": str, "mode": "list", "values": [
                    "frobenius", "kullback-leibler"]}
            }

        if setup == "custom":
            model_informations["params"] = custom_params

        model_informations["sampling"] = self._compute_param_combinations(
            model_informations["params"], n_samples)

        self.models["sklearn_nmf"] = model_informations

    def _compute_param_combinations(self, params, n_samples):
        seq = []
        changing_params = list(
            filter(lambda x: params[x]["mode"] is not "fixed", params))
        fixed_params = list(
            filter(lambda x: params[x]["mode"] is "fixed", params))

        for vec in sobol_seq.i4_sobol_generate(len(params), n_samples):
            sample = {}
            for i, name in enumerate(changing_params):
                sample[name] = self._param_to_value(
                    params[name], vec[i])
            for name in fixed_params:
                sample[name] = params[name]["values"]
            seq.append(sample)
        return seq

    def _param_to_value(self, param, sampling):
        if param["mode"] == "range":
            return self._range_to_value(param["values"], sampling, param["type"])
        if param["mode"] == "list":
            return self._list_to_value(param["values"], sampling, param["type"])

    @staticmethod
    def _range_to_value(p_range, sampling, p_type):
        value = p_range[0] + (p_range[1] - p_range[0]) * sampling
        return int(value) if p_type is int else value

    @staticmethod
    def _list_to_value(p_values, sampling, p_type):
        return p_values[min(math.floor(sampling*len(p_values)), len(p_values)-1)]

    def _compute_topic_stability(self):
        for name, settings in self.models.items():
            ranking_vecs = self._create_ranking_vectors(settings)

            for sample_id, sample in enumerate(settings["samples"]):
                n_topics = sample[0].n_components
                terms = []
                term_distributions = []

                kendalls = []
                spearman = []
                jensen = []
                wasserstein = []
                jaccard = []

                report = {}
                report_full = {}

                # Get all top terms and distributions
                for model in sample:
                    terms.append(self._get_top_terms(
                        model, self.n_relevant_top_words))

                    term_distributions.append(
                        model.components_ / model.components_.sum(axis=1)[:, np.newaxis])

                settings["topic_terms"].append(np.array(terms))

                # Evaluate each topic
                for topic in range(n_topics):
                    sim = pdist(np.array(terms)[
                        :, topic, :], self._jaccard_similarity)
                    jaccard.append(sim)

                    jen = pdist(np.array(term_distributions)[
                        :, topic, :], self._jenson_similarity)
                    jensen.append(jen)

                    wasser = pdist(np.array(term_distributions)[
                        :, topic, :], self._wasserstein_similarity)
                    wasserstein.append(wasser)

                    ken = pdist(ranking_vecs[sample_id][
                        :, topic, :], self._kendalls)
                    kendalls.append(ken)

                    spear = pdist(ranking_vecs[sample_id][
                        :, topic, :], self._spear)
                    spearman.append(spear)

                kendalls_ranking = np.array(kendalls)
                spearman_ranking = np.array(spearman)
                jaccard_similarity = np.array(jaccard)
                jensen_similarity = np.array(jensen)
                wasserstein_similarity = np.array(wasserstein)

                report["model"] = name
                report["sample_id"] = sample_id
                report["n_topics"] = n_topics
                report["params"] = settings["sampling"][sample_id]

                report["jaccard"] = jaccard_similarity.mean()
                report["kendallstau"] = kendalls_ranking.mean()
                report["spearman"] = spearman_ranking.mean()
                report["jensenshannon"] = jensen_similarity.mean()
                report["wasserstein"] = wasserstein_similarity.mean()

                report_full["model"] = name
                report_full["sample_id"] = sample_id
                report_full["n_topics"] = n_topics
                report_full["params"] = settings["sampling"][sample_id]

                report_full["jaccard"] = {
                    "mean": jaccard_similarity.mean(axis=1),
                    "std": jaccard_similarity.std(axis=1),
                    "min": jaccard_similarity.min(axis=1),
                    "max": jaccard_similarity.max(axis=1),
                }
                report_full["kendalltau"] = {
                    "mean": kendalls_ranking.mean(axis=1),
                    "std": kendalls_ranking.std(axis=1),
                    "min": kendalls_ranking.min(axis=1),
                    "max": kendalls_ranking.max(axis=1),
                }
                report_full["spearman"] = {
                    "mean": spearman_ranking.mean(axis=1),
                    "std": spearman_ranking.std(axis=1),
                    "min": spearman_ranking.min(axis=1),
                    "max": spearman_ranking.max(axis=1),
                }
                report_full["jensenshannon"] = {
                    "mean": jensen_similarity.mean(axis=1),
                    "std": jensen_similarity.std(axis=1),
                    "min": jensen_similarity.min(axis=1),
                    "max": jensen_similarity.max(axis=1),
                }
                report_full["wasserstein"] = {
                    "mean": wasserstein_similarity.mean(axis=1),
                    "std": wasserstein_similarity.std(axis=1),
                    "min": wasserstein_similarity.min(axis=1),
                    "max": wasserstein_similarity.max(axis=1),
                }

                settings["report"].append(report)
                settings["report_full"].append(report_full)

    def rank_models(self, weights=[1, 1, 1], ranking={
        "jensenshannon": 1,
        "jaccard": 1,
            "kendalltau": 1}):
        all_reports = []

        for settings in self.models.values():
            all_reports.extend(settings["report"])

        return sorted(all_reports, key=lambda s: (s["jaccard"]*weights[0] + s[self.rank_metric]*weights[1] + s[self.distribution_metric]*weights[2])/np.sum(weights), reverse=True)

    def analyse_sample(self, model, sample_id, feature_names):
        print("Intersecting words for each topic")

        # Intersect each topic
        for topic in range(len(self.models[model]["samples"][sample_id][0].components_)):
            inter = set(self.models[model]["topic_terms"][sample_id][topic][0])
            for terms in self.models[model]["topic_terms"][sample_id]:
                inter.intersection_update(set(list(terms[topic])))

            print("Topic: " + str(topic))
            print(" ".join([feature_names[i] for i in inter]))

    def display_topics(self, model, sample_id, model_number, feature_names, no_top_words):
        for topic_idx, topic in enumerate(self.models[model]["samples"][sample_id][model_number].components_):
            print("Topic %d:" % (topic_idx))
            print(" ".join([feature_names[i]
                            for i in topic.argsort()[:-no_top_words - 1:-1]]))

    def _create_ranking_vectors(self, settings):
        vocab = set()
        sample_terms = []
        ranking_vecs = []

        for sample in settings["samples"]:
            terms = []
            for model in sample:
                top_terms = self._get_top_terms(
                    model, self.n_relevant_top_words)
                terms.append(top_terms)
                vocab.update([e for l in top_terms for e in l])
            sample_terms.append(terms)

        vocab_vec = list(vocab)

        for sample in sample_terms:
            rankings = []
            for model_terms in sample:
                rankings.append([self._terms_to_ranking(t, vocab_vec)
                                 for t in model_terms])
            ranking_vecs.append(np.array(rankings))

        return ranking_vecs

    @staticmethod
    def _jaccard_similarity(a, b):
        sa = set(a)
        sb = set(b)
        return len(sa.intersection(sb))/len(sa.union(sb))

    @staticmethod
    def _kendalls(a, b):
        k, _ = kendalltau(a, b)
        return k

    @staticmethod
    def _spear(a, b):
        k, _ = spearmanr(a, b)
        return k

    @staticmethod
    def _jenson_similarity(a, b):
        distance = jensenshannon(a, b)
        return 1 - distance

    @staticmethod
    def _wasserstein_similarity(a, b):
        distance = wasserstein_distance(a, b)
        return 1 - distance

    @staticmethod
    def _terms_to_ranking(terms, vocab):
        vec = []
        for e in vocab:
            if e in terms:
                vec.append(terms.index(e))
            else:
                vec.append(len(vocab))
        return vec

    @staticmethod
    def _get_top_terms(model, n_terms):
        topic_terms = []
        for topic in model.components_:
            topic_terms.append([i for i in topic.argsort()[:-n_terms - 1:-1]])

        return topic_terms
