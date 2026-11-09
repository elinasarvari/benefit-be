select 'the marker must be exactly one row with synthetic = true' as problem
where (select count(*) from "r"."meta"."dataset_metadata") != 1
   or (select count(*) from "r"."meta"."dataset_metadata" where synthetic) != 1