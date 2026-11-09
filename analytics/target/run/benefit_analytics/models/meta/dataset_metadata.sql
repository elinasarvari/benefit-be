
    

    create  table
      "r"."meta"."dataset_metadata__dbt_tmp"
  
    
    as (
      select synthetic, seed, generator_version, generated_at
from "r"."staging"."dataset_metadata"
    );
    
  