import shutil
import typing
from collections import defaultdict

from slurminade.conf import _get_conf
from slurminade.dispatcher import _FunctionCall
from slurminade.function import SlurmFunction


class HashableDict(dict):
    """
    So we can create a dictionary of all the configurations.
    """

    def __hash__(self):
        return hash(tuple(sorted(hash((k, v)) for k, v in self.items())))


class AutoBatch:
    """
    A context manager to automatically group tasks into batches. This is useful if
    many tasks are usually very short and the overhead of Slurm would be too high.
    Just state how many tasks should be at most in one batch and then this
    manager with automatically take care of it.
    Note that you need to use `add` instead of `distribute`.
    """

    def __init__(self, max_batch_size: typing.Optional[int] = None):
        self.max_batch_size = max_batch_size
        self._tasks = defaultdict(list)
        self._on_completion = []

    def add(self, func: SlurmFunction, *args, **kwargs) -> None:
        """
        Add a function call to the batch.
        :param func: Function to be called (needs to be slurmified)
        :param args: Arguments to call the function with.
        :param kwargs: Keyword arguments to call the function with.
        :return: None
        """
        if not isinstance(func, SlurmFunction):
            raise ValueError(
                "Can only batch SlurmFunctions. Use the slurmify-decorator "
                "to convert your function."
            )
        call = _FunctionCall(func.func_id, args, kwargs)
        conf = _get_conf(dict(func.special_slurm_opts))
        self._tasks[HashableDict(conf)].append(call)

    def on_completion(self, func: SlurmFunction, *args, **kwargs) -> None:
        self._on_completion.append((func, args, kwargs))

    def __enter__(self):
        return self

    def run_locally(self):
        """
        Run the batch locally, without slurm.
        :return: None
        """
        for conf, calls in self._tasks.items():
            for call in calls:
                SlurmFunction.call(call.func_id, *call.args, **call.kwargs)
        self._tasks.clear()

    def _add_on_completion_tasks(self, job_ids):
        for sfunc, args, kwargs in self._on_completion:
            sfunc: SlurmFunction
            dep = sfunc.special_slurm_opts.get("dependency", "")
            if dep:
                dep += ",afterany:" + ":".join(str(j) for j in job_ids)
            else:
                dep = "afterany:" + ":".join(str(j) for j in job_ids)
            SlurmFunction.dispatcher.dispatch_to_slurm(
                _FunctionCall(sfunc.func_id, args, kwargs), {"dependency": dep})

    def distribute(self) -> typing.List[int]:
        """
        Distributes the tasks.
        :return: An array with the job ids.
        """
        job_ids = []
        if self.max_batch_size is None:
            for conf, calls in self._tasks.items():
                jid = SlurmFunction.dispatcher.dispatch_batch_to_slurm(calls, conf)
                job_ids.append(jid)
        else:
            for conf, calls in self._tasks.items():
                while calls:
                    jid = SlurmFunction.dispatcher.dispatch_batch_to_slurm(
                        calls[: self.max_batch_size], conf
                    )
                    job_ids.append(jid)
                    calls = calls[self.max_batch_size:]
        self._tasks.clear()
        self._add_on_completion_tasks(job_ids)
        return job_ids

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            print("Aborted due to exception.")
            return
        if not shutil.which("sbatch"):
            print("No Slurm environment available. Running batch locally.")
            self.run_locally()
        else:
            self.distribute()
