#!/bin/bash

#PBS -N compNcSht
#PBS -l walltime=24:00:00
#PBS -l mem=2000MB
#PBS -l ncpus=1
#PBS -q normal
#PBS -l wd

## Descriptions: this script finds all NETCDF version 3 files on the
## user's /short/${PROJECT}/ folder. These are then converted to
## netcdf4 and compression is applied to the individual fields. The
## compression does not lead to a loss of accuracy. This typically
## reduces the file size by about 50%

## This script can be run on the cluster at NCI via
## qsub /path/to/compress_netcdf.sh $TARGET
## or on the command line via
## /path/to/compress_netcdf.sh $TARGET

module load netcdf


tempfile=`mktemp -p . -u`
tempfilemgc=${tempfile}.mgc

cat <<EOF > $tempfilemgc
# NetCDF-V3
0  string   CDF\001    NetCDF Data Format data
# NetCDF-V3-64bit
0  string   CDF\002    NetCDF Data Format data, 64-bit offset

EOF

file -C -m $tempfilemgc

## find files              check type        netcdf only                    extract the filename           convert to netcdf4 and compress fields
find $1 -type f -exec file -m $tempfile {} \; | grep "NetCDF Data Format data" | rev | cut -d: -f2- | rev | xargs -I@ bash -c "echo @ ; nccopy -d1 @ @_nc4 ; mv @_nc4 @"

rm -f $tempfilemgc $tempfile

echo "## End of script ##"

