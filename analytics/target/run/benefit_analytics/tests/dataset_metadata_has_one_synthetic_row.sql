
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  select 'the marker must be exactly one row with synthetic = true' as problem
where (select count(*) from "r"."meta"."dataset_metadata") != 1
   or (select count(*) from "r"."meta"."dataset_metadata" where synthetic) != 1
  
  
      
    ) dbt_internal_test