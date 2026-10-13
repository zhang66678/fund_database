"""集中维护 Tushare 基金接口的请求字段, 供正式流程和探索脚本共用."""

# 显式请求基金档案的 25 个字段, 便于核对返回结构, 不依赖接口默认列.
BASIC = ('ts_code,name,management,custodian,fund_type,found_date,due_date,list_date,'
         'issue_date,delist_date,issue_amount,m_fee,c_fee,duration_year,p_value,'
         'min_amount,exp_return,benchmark,status,invest_type,type,trustee,'
         'purc_startdate,redm_startdate,market')

NAV_FIELDS = 'ts_code,ann_date,nav_date,unit_nav,accum_nav,adj_nav,accum_div'
ADJ_FIELDS = 'ts_code,trade_date,adj_factor'
DIV_FIELDS = 'ts_code,ann_date,div_proc,record_date,ex_date,pay_date,div_cash,net_ex_date,account_date'
MANAGER_FIELDS = 'ts_code,ann_date,name,begin_date,end_date,edu,resume'
ASSET_FIELDS = 'ts_code,ann_date,nav_date,net_asset,total_netasset'
