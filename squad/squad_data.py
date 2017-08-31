import pickle
from os import makedirs, listdir
from os.path import isfile, join, exists, isdir
from typing import List, Tuple, Optional, Dict

import numpy as np

from config import CORPUS_DIR
from configurable import Configurable
from data_processing.qa_training_data import ParagraphAndQuestionSpec, Answer, ParagraphQaTrainingData, ContextAndQuestion
from data_processing.span_data import ParagraphSpans
from data_processing.word_vectors import load_word_vectors
from utils import ResourceLoader, flatten_iterable


"""
Represent SQuAD data
"""


class Question(object):
    """ Question paired with its answer """

    def __init__(self, question_id: str, words: List[str], answer: ParagraphSpans):
        self.question_id = question_id
        self.words = words
        self.answer = answer

    def __repr__(self) -> str:
        return " ".join(self.words)


class Paragraph(object):
    """ Context with multiple questions, optionally includes it's "raw" untokenzied/un-normalized text and the reverse
    mapping for the tokenized text -> raw text """

    def __init__(self,
                 context: List[List[str]],
                 questions: List[Question],
                 article_id: str,
                 paragraph_num: int,
                 original_text: Optional[str] = None,
                 spans: Optional[np.ndarray] = None):
        self.article_id = article_id
        self.context = context
        self.questions = questions
        self.paragraph_num = paragraph_num
        self.original_text = original_text
        self.spans = spans

    def get_original_text(self, start, end):
        """ Get text between the token at `start` and `end` inclusive """
        return self.original_text[self.spans[start][0]:self.spans[end][1]]

    @property
    def n_context_words(self):
        return sum(len(x) for x in self.context)

    def __repr__(self) -> str:
        return "Paragraph%d(%s...)" % (self.paragraph_num, self.context[0][:40])


class Document(object):
    """ Collection of paragraphs """

    def __init__(self, doc_id: str, title: str, paragraphs: List[Paragraph]):
        self.title = title
        self.doc_id = doc_id
        self.paragraphs = paragraphs

    def __repr__(self) -> str:
        return "Document(%s)" % self.title


class DocParagraphAndQuestion(ContextAndQuestion):

    def __init__(self, question: List[str], answer: Optional[Answer],
                 question_id: str, paragraph: Paragraph):
        super().__init__(question, answer, question_id)
        self.paragraph = paragraph

    def get_original_text(self, para_start, para_end):
        return self.paragraph.get_original_text(para_start, para_end)

    def get_context(self):
        return flatten_iterable(self.paragraph.context)

    @property
    def n_context_words(self) -> int:
        return sum(len(s) for s in self.paragraph.context)

    @property
    def paragraph_num(self):
        return self.paragraph.paragraph_num

    @property
    def article_id(self):
        return self.paragraph.article_id


def split_docs(docs: List[Document]) -> List[DocParagraphAndQuestion]:
    paras = []
    for doc in docs:
        for i, para in enumerate(doc.paragraphs):
            for question in para.questions:
                paras.append(DocParagraphAndQuestion(question.words, question.answer, question.question_id, para))
    return paras


class SquadCorpus(Configurable):
    # Pickle seems faster the json for loading
    TRAIN_FILE = "train.pkl"
    DEV_FILE = "dev.pkl"

    VOCAB_FILE = "vocab.txt"
    WORD_VEC_SUFFIX = "_pruned"

    @staticmethod
    def make_corpus(train: List[Document],
                    dev: List[Document]):
        dir = join(CORPUS_DIR, "squad")
        if isfile(dir) or (exists(dir) and len(listdir(dir))) > 0:
            raise ValueError("Directory %s already exists and is non-empty" % dir)
        if not exists(dir):
            makedirs(dir)

        for name, data in [(SquadCorpus.TRAIN_FILE, train), (SquadCorpus.DEV_FILE, dev)]:
            if data is not None:
                with open(join(dir, name), 'wb') as f:
                    pickle.dump(data, f)

    def __init__(self):
        dir = join(CORPUS_DIR, "squad")
        if not exists(dir) or not isdir(dir):
            raise ValueError("No directory %d, corpus not built yet?" + dir)
        self.dir = dir

    @property
    def evidence(self):
        return None

    def get_vocab(self):
        """ get all-lower cased unique words for this corpus, includes train/dev/test files """
        voc_file = join(self.dir, self.VOCAB_FILE)
        if exists(voc_file):
            with open(voc_file, "r") as f:
                return [x.rstrip() for x in f]
        else:
            voc = set()
            for fn in [self.get_train, self.get_dev, self.get_test]:
                for doc in fn():
                    for para in doc.paragraphs:
                        for sent in para.context:
                            voc.update(x.lower() for x in sent)
                        for question in para.questions:
                            voc.update(x.lower() for x in question.words)
                            voc.update(x.lower() for x in question.answer.get_vocab())
            voc_list = sorted(list(voc))
            with open(voc_file, "w") as f:
                for word in voc_list:
                    f.write(word)
                    f.write("\n")
            return voc_list

    def get_pruned_word_vecs(self, word_vec_name, voc=None):
        """
        Loads word vectors that have been pruned to the case-insensitive vocab of this corpus.
        WARNING: this includes test/dev words

        This exists since loading word-vecs each time we startup can be a big pain, so
        we cache the pruned vecs on-disk as a .npy file we can re-load quickly.
        """

        vec_file = join(self.dir, word_vec_name + self.WORD_VEC_SUFFIX + ".npy")
        if isfile(vec_file):
            print("Loading word vec %s for %s from cache" % (word_vec_name, self.name))
            with open(vec_file, "rb") as f:
                return pickle.load(f)
        else:
            print("Building pruned word vec %s for %s" % (self.name, word_vec_name))
            voc = self.get_vocab()
            vecs = load_word_vectors(word_vec_name, voc)
            with open(vec_file, "wb") as f:
                pickle.dump(vecs, f)
            return vecs

    def get_resource_loader(self):
        return ResourceLoader(self.get_pruned_word_vecs)

    def get_train(self) -> List[Document]:
        return self._load(join(self.dir, self.TRAIN_FILE))

    def get_dev(self) -> List[Document]:
        return self._load(join(self.dir, self.DEV_FILE))

    def get_test(self) -> List[Document]:
        return []

    def _load(self, file) -> List[Document]:
        if not exists(file):
            return []
        with open(file, "rb") as f:
            return pickle.load(f)


class DocumentQaTrainingData(ParagraphQaTrainingData):
    def _preprocess(self, x):
        data = split_docs(x)
        return data, len(data)


def compute_document_voc(data: List[Document]):
    voc = set()
    for doc in data:
        for para in doc.paragraphs:
            for sent in para.context:
                voc.update(sent)
            for question in para.questions:
                voc.update(question.words)
                voc.update(question.answer.get_vocab())
    return voc


def get_doc_input_spec(batch_size, data: List[List[Document]]) -> ParagraphAndQuestionSpec:
    max_num_sents = 0
    max_sent_size = 0
    max_ques_size = 0
    max_word_size = 0
    max_para_size = 0
    for docs in data:
        for doc in docs:
            for para in doc.paragraphs:
                max_num_sents = max(max_num_sents, len(para.context))
                max_sent_size = max(max_sent_size, max(len(s) for s in para.context))
                max_word_size = max(max_word_size, max(len(word) for sent in para.context for word in sent))
                max_para_size = max(max_para_size, sum(len(sent) for sent in para.context))
                for question in para.questions:
                    max_ques_size = max(max_ques_size, len(question.words))
                    max_word_size = max(max_word_size, max(len(word) for word in question.words))
    return ParagraphAndQuestionSpec(batch_size, max_ques_size, max_para_size, max_word_size)
