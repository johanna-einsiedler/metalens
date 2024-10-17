library("duckdb")
library("jsonlite")
library(metadat)
library(metafor)
library(hash)
library(jsonlite)
library(dplyr)
library(netmeta)
# to start an in-memory database

drv <- duckdb(dbdir='/Users/htr365/no_icloud/metaness_observable/docs/data/study_data.db')
con <- dbConnect(drv)

dat <- dat.axfors2021
dat <- as.data.frame(escalc(measure="OR", ai=hcq_arm_event, n1i=hcq_arm_total,
              ci=control_arm_event, n2i=control_arm_total, data=dat))

duckdb::dbWriteTable(con,"axfors2021",dat)
dbDisconnect(con)
