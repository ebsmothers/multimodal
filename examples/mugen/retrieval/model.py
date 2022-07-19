# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import warnings
from typing import Any, Tuple

import torch

from examples.mugen.retrieval.video_clip import videoclip
from pytorch_lightning import LightningModule
from torchmetrics import Recall

from torchmultimodal.modules.losses.contrastive_loss_with_temperature import (
    ContrastiveLossWithTemperature,
)


class VideoCLIPLightningModule(LightningModule):
    """PyTorch Lightning module for evaluating VideoCLIP model.
    Args:
        logit_scale (float): Initial log-temperature value for contrastive loss funtion.
            Defaults to ``0.07``, MUGEN's log-temperature value at initialization.
        logit_scale_max (float): Maximum log-temperature value for contrastive loss function.
            Defaults to ``100``, MUGEN's maximum log-temperature value.
        recall_ks (Tuple[int]): tuple of top-``k``'s for calculating recall in evaluation.
            Defaults to ``(1, 5, 10)``, i.e. top-1 recall, top-5 recall, and top-10 recall.
        **videoclip_kwargs (Any): Keyword arguments for the videoCLIP model builder.
    """

    def __init__(
        self,
        logit_scale: float = 0.07,
        logit_scale_max: float = 100,
        recall_ks: Tuple[int] = (1, 5, 10),
        **videoclip_kwargs: Any,
    ):
        super().__init__()
        self.model = videoclip(**videoclip_kwargs)
        self.contrastive_loss = ContrastiveLossWithTemperature(
            logit_scale=logit_scale,
            logit_scale_min=None,
            logit_scale_max=logit_scale_max,
        )

        self.recall_ks = set(recall_ks)
        if len(self.recall_ks) != len(recall_ks):
            warnings.warn("Duplicate `k` values in `recall_ks` are ignored.")
        self.metrics = torch.nn.ModuleDict()
        for k in self.recall_ks:
            self.metrics.update(
                {f"v2t_recall_{k}": Recall(top_k=k), f"t2v_recall_{k}": Recall(top_k=k)}
            )

    def _collect_embeddings(self, outputs):
        text_embeddings = [batch.embeddings_a for batch in outputs]
        video_embeddings = [batch.embeddings_b for batch in outputs]

        embeddings = {
            "text": torch.cat(text_embeddings),
            "video": torch.cat(video_embeddings),
        }
        return embeddings

    def _compute_recall(self, text_embedding, video_embedding):
        similarity_matrix = text_embedding @ video_embedding.T
        num_samples = similarity_matrix.shape[0]
        target_matrix = torch.diag(torch.ones(num_samples)).to(dtype=int)

        for k in self.recall_ks:
            v2t_recall = self.metrics[f"v2t_recall_{k}"]
            v2t_recall(preds=similarity_matrix.T, target=target_matrix)
            self.log(f"Recall@{k} (video query, text retrieval)", v2t_recall)

            t2v_recall = self.metrics[f"t2v_recall_{k}"]
            t2v_recall(preds=similarity_matrix, target=target_matrix)
            self.log(f"Recall@{k} (text query, video retrieval)", t2v_recall)

    def test_step(self, batch, batch_idx):
        text, video = batch.get("text"), batch.get("video")
        model_output = self.model(features_a=text, features_b=video)
        return model_output

    def test_epoch_end(self, outputs):
        all_embeddings = self._collect_embeddings(outputs)
        text_embedding, video_embedding = (
            all_embeddings["text"],
            all_embeddings["video"],
        )
        self._compute_recall(text_embedding, video_embedding)
