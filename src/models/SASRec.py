# -*- coding: UTF-8 -*-

import torch
import numpy as np

from models.GRU4Rec import GRU4Rec
from utils import utils


class SASRec(GRU4Rec):
    @staticmethod
    def parse_model_args(parser, model_name='SASRec'):
        parser.add_argument('--num_layers', type=int, default=1,
                            help='Number of self-attention layers.')
        return GRU4Rec.parse_model_args(parser, model_name)

    def __init__(self, args, corpus):
        self.max_his = args.history_max
        self.num_layers = args.num_layers
        self.dropout = args.dropout
        self.len_range = utils.numpy_to_torch(np.arange(self.max_his))
        GRU4Rec.__init__(self, args, corpus)

    def _define_params(self):
        self.i_embeddings = torch.nn.Embedding(self.item_num, self.emb_size)
        self.p_embeddings = torch.nn.Embedding(self.max_his + 1, self.emb_size)
        self.embeddings = ['i_embeddings', 'p_embeddings']

        self.Q = torch.nn.Linear(self.emb_size, self.emb_size, bias=False)
        self.K = torch.nn.Linear(self.emb_size, self.emb_size, bias=False)
        self.W1 = torch.nn.Linear(self.emb_size, self.emb_size)
        self.W2 = torch.nn.Linear(self.emb_size, self.emb_size)

        self.dropout_layer = torch.nn.Dropout(p=self.dropout)
        self.layer_norm = torch.nn.LayerNorm(self.emb_size)

    def forward(self, feed_dict):
        self.check_list, self.embedding_l2 = [], []
        i_ids = feed_dict['item_id']          # [batch_size, -1]
        history = feed_dict['history_items']  # [batch_size, history_max]
        lengths = feed_dict['lengths']        # [batch_size]
        batch_size, seq_len = history.shape

        valid_his = (history > 0).byte()
        i_vectors = self.i_embeddings(i_ids)
        his_vectors = self.i_embeddings(history)
        self.embedding_l2.extend([i_vectors, his_vectors])

        # position embedding
        # lengths:  [4, 2, 5]
        # position: [[4, 3, 2, 1, 0], [2, 1, 0, 0, 0], [5, 4, 3, 2, 1]]
        position = self.len_range[:seq_len].unsqueeze(0).repeat(batch_size, 1)
        position = (lengths[:, None] - position) * valid_his.long()
        pos_vectors = self.p_embeddings(position)
        his_vectors = his_vectors + pos_vectors
        self.embedding_l2.append(pos_vectors)

        # self-attention
        attention_mask = 1 - valid_his.unsqueeze(1).repeat(1, seq_len, 1)
        for i in range(self.num_layers):
            residual = his_vectors
            query, key = self.Q(his_vectors), self.K(his_vectors)  # [batch_size, history_max, emb_size]
            # self-attention
            scale = self.emb_size ** -0.5
            context = utils.scaled_dot_product_attention(query, key, key, scale=scale, attn_mask=attention_mask)
            # mlp forward
            context = self.W1(context).relu()
            his_vectors = self.W2(context)  # [batch_size, history_max, emb_size]
            # dropout, residual and layer_norm
            his_vectors = self.dropout_layer(his_vectors)
            his_vectors = self.layer_norm(residual + his_vectors)

        his_vector = (his_vectors * valid_his[:, :, None].double()).sum(1)  # [batch_size, emb_size]
        his_vector = his_vector / lengths[:, None].double()

        prediction = (his_vector[:, None, :] * i_vectors).sum(-1)

        out_dict = {'prediction': prediction.view(batch_size, -1), 'check': self.check_list}
        return out_dict
