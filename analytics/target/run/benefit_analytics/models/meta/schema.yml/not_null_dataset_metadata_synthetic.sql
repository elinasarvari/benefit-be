
    
    select
      count(*) as failures,
      count(*) != 0 as should_warn,
      count(*) != 0 as should_error
    from (
      
    
  
    
    



select synthetic
from "r"."meta"."dataset_metadata"
where synthetic is null



  
  
      
    ) dbt_internal_test