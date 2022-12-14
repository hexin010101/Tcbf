import os.path
import sys

import pyfastx
from pandas import DataFrame
from pandas import read_table
import pandas as pd
import click
from tcbf.run_command import run_command

"""
python extract_TAD_bound.py  -t /data/A_genome_tad.txt \
-g /data/Garboreum_genome_HAU_v1.0/Lachesis_assembly_changed.fa -d 80000 -p A -o ~/data

"""


class TAD:
    def __init__(self,
                 chromosome: str,
                 start: int,
                 end: int,
                 distance: int = None,
                 name=None,

                 ):
        self._position = None
        self.name = name
        self.chromosome = chromosome
        self.start = start
        self.end = end

        self.distance = distance
        self.length = self.end - self.start + 1

        self._left_coord = (max(0, self.start - self.distance),
                            self.start + self.distance)
        self._right_coord = (max(0, self.end - self.distance),
                             self.end + self.distance)
        self._left_coord = tuple(int(i) for i in self._left_coord)
        self._right_coord = tuple(int(i) for i in self._right_coord)

    @property
    def position(self):
        return self._position

    def set_position(self, position=None):
        self._position = position

    @property
    def left_bound(self):
        return self.chromosome, *self._left_coord

    @property
    def right_bound(self):
        return self.chromosome, *self._right_coord

    def __repr__(self):
        return f"{self.name}\t {self.chromosome}\t{self.start}\t{self.end} Length:{self.length}\t\n" \
               f"Left bound:\t{self.left_bound}Bound length:\t{self.right_bound[2] - self.left_bound[1] + 1}\n" \
               f"Right bound:\t{self.right_bound}\n"


def bed2position(chrom, start, end):
    return f"{chrom}:{start + 1}-{end}"


def format_seq(seq):
    return "".join(seq[i:i + 60] + "\n" for i in range(0, len(seq), 60))


def add_prefix(fa, prefix, out):
    seq = pyfastx.Fasta(fa)
    with open(out, "w") as f:
        for line in seq:
            f.write(f">{prefix}_{line.name}\n{format_seq(line.seq.upper())}")


def mash_genome(genome_fasta):
    command = f"mash sketch {genome_fasta}"
    run_command(command)


class TADs:
    def __init__(self, tad_file: str,
                 fasta_file: str,

                 distance: int = None,
                 prefix: str = "TAD",
                 ):

        self.TAD_bound_table = None
        self.prefix = prefix

        tad_table = read_table(tad_file, header=None)
        tad_table.columns = ("chromosome", "start", "end")
        tad_table["chromosome"] = tad_table["chromosome"].astype(str)
        tad_table["chromosome"] = prefix + "_" + tad_table["chromosome"]

        self.distance = distance
        # ?????????????????????TAD??????
        self.TADs = []
        # ????????????dataframe
        tad_infos = []
        num = 1
        for line in tad_table.groupby("chromosome"):
            data = []
            tad_info = []
            line[1].reset_index(drop=True, inplace=True)
            for info in line[1].itertuples():
                tad = TAD(*list(info[1:]),
                          distance=distance,
                          name=self.prefix + f"_{num}")
                num += 1
                data.append(tad)
                tad_info.append((tad.name, *tad.left_bound[1:], *tad.right_bound[1:]))
            tad_bound = DataFrame(tad_info)

            tad_bound.columns = "tad_name left_start left_end right_start right_end".split()

            ss = pd.concat([line[1], tad_bound], axis=1)

            tad_infos.append(ss)

            data[0].set_position("first")
            data[-1].set_position("last")

            self.TADs.extend(data)

        self.TAD_table = pd.concat(tad_infos)

        from pysam import Fastafile
        self.fasta = Fastafile(fasta_file)

    def export_table(self, FileName):
        self.TAD_table.to_csv(FileName + ".csv", index=False)

    def _extract_seq(self, chrom, start, end):

        position = bed2position(chrom, start, end)
        seq = self.fasta.fetch(region=position)
        return format_seq(seq.strip("N"))

    def extract_bound_bed(self, handle):
        s1 = self.TAD_table.loc[:, ["chromosome", "left_start", "left_end"]]
        s2 = self.TAD_table.loc[:, ["chromosome", "right_start", "right_end"]]
        s1.columns = ("chromosome", "start", "end")
        s2.columns = s1.columns
        self.TAD_bound_table = pd.concat([s1, s2], axis=0).drop_duplicates().sort_values(["chromosome", "start"]). \
            reset_index(drop=True)
        self.TAD_bound_table = self.TAD_bound_table.groupby("chromosome").apply(
            lambda x: pd.DataFrame(merge(x.iloc[:, 1:].apply(tuple, axis=1).to_list()))).reset_index().iloc[:,
                               [0, 2, 3]]
        self.TAD_bound_table.columns = ("chromosome", "start", "end")

        self.TAD_bound_table.index = self.TAD_bound_table.index.set_names(["tad_name"])

        self.TAD_bound_table.reset_index(inplace=True)
        self.TAD_bound_table["tad_name"] = self.prefix + "_bound_" + self.TAD_bound_table["tad_name"].astype(str)

        self.TAD_bound_table.to_csv(handle, index=False)

    def extract_bound_seq(self, out_fasta):
        for tad_bound in self.TAD_bound_table.itertuples():
            seq = self._extract_seq(*tad_bound[2:])
            out_fasta.write(f">{tad_bound[1]} {tad_bound[2:]}\n{seq}\n")


def merge(intervals):
    """
    :type intervals: List[Interval]
    :rtype: List[Interval]
    """

    # genearl idea:
    # sort the first element of all intervals as [starts]
    # sort the second element of all intervals as [ends]
    # e.g. [1,3], [5,8], [4,7], [9, 10] becomes
    # starts=[1,4,5,9], ends=[3,7,8,10]
    # then compare ends[i] with start[i+1]

    if len(intervals) <= 1:
        return intervals

    starts = []
    ends = []

    for interval in intervals:
        start, end = interval
        starts.append(start)
        ends.append(end)

    starts.sort()
    ends.sort()
    results = []
    start = starts[0]
    end = ends[0]
    for i in range(len(intervals)):
        try:
            if ends[i] < starts[i + 1]:
                results.append((start, end))
                start = starts[i + 1]
            end = ends[i + 1]
        except IndexError:  # in this case, we have reached the last element of starts and ends
            results.append((start, end))

    return results


def extract_TAD_boundary(tad: str,
                         genome: str,
                         distance: int,
                         prefix: str,
                         output: str,
                         skip: bool = False):
    if not os.path.exists(output):
        os.mkdir(output)
    if not os.path.exists(os.path.join(output, "Step1")):
        os.mkdir(os.path.join(output, "Step1"))

    output = os.path.join(output, "Step1")
    genome_file = os.path.join(output, f"{prefix}.genome.fa")
    if not skip:
        add_prefix(genome, prefix, genome_file)
        mash_genome(genome_file)
    G1_TAD = TADs(tad,
                  genome_file,
                  distance=distance,
                  prefix=prefix,
                  )

    G1_TAD.export_table(os.path.join(output, f"{prefix}.TAD"))

    with open(os.path.join(output, f"{prefix}.bound.bed", ), "w") as f:
        G1_TAD.extract_bound_bed(f)
    with open(os.path.join(output, f"{prefix}.bound.fasta", ), "w") as f:
        G1_TAD.extract_bound_seq(f)


@click.command()
@click.option("-t", '--TAD', type=click.Path(exists=True), help="TAD file ", required=True)
@click.option("-g", '--genome', type=click.Path(exists=True), help="genome sequence file  ", required=True)
@click.option('-d', "--distance", type=int, default=40000, required=False)
@click.option('-p', "--prefix", type=str, default=None, help="?????????????????????", required=True)
@click.option('-o', "--output", type=str, default='out', help="?????????????????????", required=False)
def main(tad: str,
         genome: str,
         distance: int,
         prefix: str,
         output: str,
         not_need: bool):
    extract_TAD_boundary(tad, genome, distance, prefix, output, not_need)


if __name__ == '__main__':
    pd.options.mode.chained_assignment = None
    main()
